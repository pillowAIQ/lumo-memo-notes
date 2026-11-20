"""半透明桌面待办挂件：常驻屏幕一角，显示未完成的备忘录。"""
from __future__ import annotations

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    Qt,
    QRectF,
    QSize,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import db, effects, nlp_time, theme
from .db import Memo
from .icons import ui_icon

MAX_ROWS = 8
GROUP_MAX_ROWS = 6
# 列表滚动区高度上限 = min(绝对上限, 可用屏高 - 面板固定部分(标题栏/边距/提示行) - 上下安全边距)
_SCROLL_MAX = 420
_SCROLL_RESERVE = 170
_SCROLL_MIN = 120
# 面板允许部分移出屏幕，但至少保留这么多像素在屏幕内，方便再拖回来
_KEEP_VISIBLE = 72
_KEEP_VISIBLE_Y = 44  # 标题栏高度：顶部不允许移出屏幕

# 挂件配色随风格：云母 = 深色亚克力（文字浅色），纸笺 = 钉在桌面上的纸条（文字墨色）
_MICA_COLORS = {
    "panel": (30, 36, 48, 190), "edge": (255, 255, 255, 40),
    "title": "#F5F7FB", "count": "#B9C2D0", "item": "#EEF1F6", "meta": "#AEB8C6", "hint": "#97A2B3",
    "group": "#DCE3EE", "icon": "#C3CCD9", "ring": "#9AA5B5", "ring_hover": "#C8D6F0",
    "hover": "rgba(255, 255, 255, 18)", "hover_strong": "rgba(255, 255, 255, 32)",
    "press": "rgba(255, 255, 255, 50)", "focus": "#8FB2F5", "pin": "#F2C66D",
    "font_title": "", "font_body": "",
}
_PAPER_COLORS = {
    "panel": (251, 247, 238, 238), "edge": (120, 95, 60, 60),
    "title": "#2A2521", "count": "#B4412E", "item": "#3A322B", "meta": "#83786B", "hint": "#A29788",
    "group": "#5B5048", "icon": "#83786B", "ring": "#6B6157", "ring_hover": "#B4412E",
    "hover": "rgba(120, 95, 60, 20)", "hover_strong": "rgba(120, 95, 60, 34)",
    "press": "rgba(120, 95, 60, 52)", "focus": "#C9735F", "pin": "#B4412E",
    "font_title": 'font-family: "Noto Serif SC", "STSong", "SimSun", serif;',
    "font_body": 'font-family: "Noto Serif SC", "STSong", "Microsoft YaHei UI", serif;',
}


def wcolor(key: str):
    return (_PAPER_COLORS if theme.is_paper() else _MICA_COLORS)[key]


class GroupHeader(QWidget):
    """挂件里的分类分组头：点击切换展开/折叠。"""

    toggled = Signal(str)

    def __init__(self, key: str, title: str, count: int, expanded: bool) -> None:
        super().__init__()
        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setFixedHeight(30)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        self.arrow = QLabel()
        self.arrow.setPixmap(ui_icon("chevron_down" if expanded else "chevron_right", wcolor("icon"), 12).pixmap(12, 12))
        self.arrow.setObjectName("widgetGroupArrow")
        self.full_title = title
        self.name = QLabel(title)
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name.setObjectName("widgetGroupName")
        self.setToolTip(title)
        cnt = QLabel(str(count))
        cnt.setObjectName("widgetGroupCount")
        for w in (self.arrow, self.name, cnt):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.arrow)
        lay.addWidget(self.name, 1)
        lay.addWidget(cnt)

    def set_state(self, expanded: bool, count: int) -> None:
        self.arrow.setPixmap(ui_icon("chevron_down" if expanded else "chevron_right", wcolor("icon"), 12).pixmap(12, 12))
        # 右侧计数在 layout 最后一个 widget
        self.layout().itemAt(self.layout().count() - 1).widget().setText(str(count))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self.name.setText(self.name.fontMetrics().elidedText(
            self.full_title, Qt.TextElideMode.ElideRight, self.name.width()
        ))

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggled.emit(self.key)
            return
        super().keyPressEvent(e)

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


class _ListScrollArea(QScrollArea):
    """列表滚动区：sizeHint 高度 = min(内容高度, maximumHeight)。

    QScrollArea 默认 sizeHint 不随内容增长，adjustSize() 会把面板
    压得过小；这里让矮内容保持紧凑、高内容撑到上限后在内部滚动。
    """

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        content = self.widget()
        if content is None:
            return hint
        h = content.sizeHint().height()
        bound = self.maximumHeight()
        if bound > 0:
            h = min(h, bound)
        return QSize(hint.width(), max(0, h))


class TodoWidget(QWidget):
    """无边框置顶半透明小面板。可拖动，双击空白处打开主界面。"""

    open_main = Signal()
    done_toggled = Signal(int)  # memo_id
    dismissed = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._minimized = False
        self.setFixedWidth(300)
        self.setObjectName("todoWidget")
        self._qss = (
            """
            /* 面板背景由 paintEvent 手绘（半透明顶层窗口 QSS 背景不生效） */
            QLabel { background: transparent; border: none; }
            QLabel#widgetTitle { font-size: 14px; font-weight: bold; color: @title@; @font_title@ }
            QLabel#widgetCount { font-size: 11px; color: @count@; }
            QLabel#widgetItem { font-size: 12px; color: @item@; @font_body@ }
            QLabel#widgetMeta { font-size: 11px; color: @meta@; }
            QLabel#widgetHint { font-size: 10px; color: @hint@; }
            QLabel#widgetGroupName { font-size: 12px; font-weight: bold; color: @group@; @font_title@ }
            QLabel#widgetGroupArrow { font-size: 9px; color: @icon@; }
            QLabel#widgetGroupCount { font-size: 10px; color: @meta@; }
            GroupHeader { background: transparent; border: 1px solid transparent; border-radius: 6px; }
            GroupHeader:hover { background: @hover@; }
            GroupHeader:focus { background: @hover_strong@; border-color: @focus@; }
            QWidget#widgetRow { background: transparent; border-radius: 6px; }
            QWidget#widgetRow:hover { background: @hover@; }
            QPushButton#widgetBtn {
                border: none; background: transparent; color: @icon@;
                border-radius: 5px; padding: 0;
            }
            QPushButton#widgetBtn:hover, QPushButton#widgetBtn:focus { background: @hover_strong@; }
            QPushButton#widgetBtn:pressed { background: @press@; }
            QPushButton#widgetDone {
                border: 1.5px solid @ring@;
                border-radius: 9px;
                background: transparent; color: transparent;
                font-size: 10px; font-weight: bold; padding: 0;
            }
            QPushButton#widgetDone:hover, QPushButton#widgetDone:focus {
                border-color: @ring_hover@; background: @hover_strong@;
            }
            QPushButton#widgetDone[done="true"] {
                background: @ACCENT@; border-color: @ACCENT@; color: white;
            }
            QPushButton#widgetMore {
                border: 1px solid transparent; background: transparent;
                color: @meta@; font-size: 11px; border-radius: 6px;
                padding: 4px 10px; text-align: left;
            }
            QPushButton#widgetMore:hover, QPushButton#widgetMore:focus {
                background: @hover_strong@; color: @item@;
            }
            QPushButton#widgetMore:pressed { background: @press@; }
            QScrollArea#widgetScroll { background: transparent; border: none; }
            QScrollArea#widgetScroll > QWidget > QWidget { background: transparent; }
            """
        )
        self._apply_style()
        theme.notifier.changed.connect(self._apply_style)
        self._build()
        self._press_pos = None
        self._dragging = False
        # 由 Controller 注入：返回非回收站的全部备忘录
        self._memo_provider = None
        # 分组展开状态（会话内记忆，重启默认全部折叠 = 隐私默认）
        self._expanded: set[str] = set()
        # 分组"显示全部"状态：与折叠/展开相互独立，同样会话内记忆
        self._show_all: set[str] = set()
        self._groups: dict[str, dict] = {}
        # 已点完成、正在播放打勾反馈的条目：防止重建后重复点击导致来回切换
        self._pending_done: set[int] = set()
        # 连续点击时合并窗口尺寸调整，避免半透明面板反复重绘卡顿
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(40)
        self._resize_timer.timeout.connect(self._relayout)
        self._move_to_corner()

    def _apply_style(self, *_args) -> None:
        # 面板外观随风格（深色亚克力 / 纸条）；勾选色跟随皮肤强调色（深色皮肤用其深色版本保证白勾对比）
        accent = theme.c("accent_press" if theme.is_dark() else "accent")
        qss = self._qss.replace("@ACCENT@", accent)
        for key in ("title", "count", "item", "meta", "hint", "group", "icon", "ring", "ring_hover",
                    "hover_strong", "hover", "press", "focus", "font_title", "font_body"):
            qss = qss.replace(f"@{key}@", wcolor(key))
        self.setStyleSheet(qss)
        if hasattr(self, "min_btn"):
            self._sync_header_icons()
            self.reload()
        self.update()

    def _sync_header_icons(self) -> None:
        self.min_btn.setIcon(ui_icon("restore_window" if self._minimized else "minus", wcolor("icon"), 14))
        self.hide_btn.setIcon(ui_icon("close", wcolor("icon"), 14))

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.title_label = QLabel("小小待办")
        self.title_label.setObjectName("widgetTitle")
        self.count_label = QLabel("")
        self.count_label.setObjectName("widgetCount")
        for _w in (self.title_label, self.count_label):
            _w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header.addWidget(self.title_label)
        header.addWidget(self.count_label)
        header.addStretch(1)
        self.min_btn = QPushButton()
        self.min_btn.setIcon(ui_icon("minus", wcolor("icon"), 14))
        self.min_btn.setObjectName("widgetBtn")
        self.min_btn.setFixedSize(22, 22)
        self.min_btn.setAccessibleName("最小化桌面挂件")
        self.min_btn.setToolTip("最小化为标题栏")
        self.hide_btn = QPushButton()
        self.hide_btn.setIcon(ui_icon("close", wcolor("icon"), 14))
        self.hide_btn.setAccessibleName("隐藏桌面挂件")
        self.hide_btn.setObjectName("widgetBtn")
        self.hide_btn.setFixedSize(22, 22)
        self.hide_btn.setToolTip("隐藏挂件（托盘菜单可再打开）")
        header.addWidget(self.min_btn)
        header.addWidget(self.hide_btn)
        root.addLayout(header)

        self.list_panel = QWidget()
        self.list_host = QVBoxLayout(self.list_panel)
        self.list_host.setSpacing(2)
        self.list_host.setContentsMargins(0, 2, 0, 0)
        # 列表放进有界滚动区：内容变高时内部滚动，面板本身不越出屏幕；
        # 内容矮小时滚动区自动收缩，保持原有紧凑外观
        self.list_scroll = _ListScrollArea()
        self.list_scroll.setObjectName("widgetScroll")
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_scroll.setWidget(self.list_panel)
        root.addWidget(self.list_scroll)

        self.more_label = QLabel("")
        self.more_label.setObjectName("widgetHint")
        self.more_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self.more_label)

        self.hint_label = QLabel("双击打开 · 按住拖动")
        self.hint_label.setObjectName("widgetHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self.hint_label)

        self.min_btn.clicked.connect(self._toggle_minimized)
        self.hide_btn.clicked.connect(self._dismiss)
        for button in (self.min_btn, self.hide_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)

    def _set_list_visible(self, on: bool) -> None:
        self.list_scroll.setVisible(on)
        self.list_panel.setVisible(on)

    def _toggle_minimized(self) -> None:
        self._minimized = not self._minimized
        self._set_list_visible(not self._minimized)
        self.hint_label.setVisible(not self._minimized)
        self._sync_header_icons()
        self.min_btn.setAccessibleName("还原桌面挂件" if self._minimized else "最小化桌面挂件")
        self.min_btn.setToolTip("还原待办列表" if self._minimized else "最小化为标题栏")
        self._resize_timer.stop()
        self._relayout()

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()

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

        # 按分类分组：未分类("0")排最后
        cats = {c.id: c for c in db.list_categories()}
        cat_map = db.memo_category_map()
        cat_rank = {cid: i for i, cid in enumerate(cats)}

        def group_key(m: Memo) -> str:
            cids = cat_map.get(m.id, set()) & cats.keys()
            return str(min(cids, key=lambda c: cat_rank[c])) if cids else "0"

        groups: dict[str, list[Memo]] = {}
        for m in todos:
            groups.setdefault(group_key(m), []).append(m)
        # 分组顺序跟随侧边栏的分类排序（可拖动调整）；未分类排最后
        rank = {str(cid): i for i, cid in enumerate(cats)}
        ordered = sorted(groups.keys(), key=lambda k: (k == "0", rank.get(k, len(rank))))

        for key in ordered:
            memos = groups[key]
            title = "未分类" if key == "0" else cats[int(key)].name
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
                self._fill_group(bl, memos, key)
            self.list_host.addWidget(body)
            self._groups[key] = {"hdr": hdr, "body": body, "memos": memos}

        self.more_label.setVisible(False)
        self._relayout()

    def _fill_group(self, lay: QVBoxLayout, memos: list[Memo], key: str) -> None:
        show_all = key in self._show_all
        for memo in (memos if show_all else memos[:GROUP_MAX_ROWS]):
            lay.addWidget(self._make_row(memo))
        rest = len(memos) - GROUP_MAX_ROWS
        if show_all:
            # 全部展开后提供"收起"回到前 6 条；条目不超过 6 条时无需按钮
            if rest > 0:
                lay.addWidget(self._more_button(key, "收起", True, rest))
        elif rest > 0:
            lay.addWidget(self._more_button(key, f"… 还有 {rest} 条", False, rest))

    def _more_button(self, key: str, text: str, expanded: bool, rest: int) -> QPushButton:
        """分组底部的"展开全部/收起"按钮：可点击、可 Tab 聚焦、可用空格/回车触发。"""
        btn = QPushButton(text)
        btn.setObjectName("widgetMore")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setAccessibleName(
            f"收起，返回显示前 {GROUP_MAX_ROWS} 条" if expanded
            else f"展开全部 {rest} 条备忘录"
        )
        btn.setToolTip(btn.accessibleName())
        btn.clicked.connect(lambda _c=False, k=key: self._toggle_show_all(k))
        return btn

    def _toggle_show_all(self, key: str) -> None:
        """只切换该组的"显示全部"并重建本组行，不动分组折叠状态。"""
        g = self._groups.get(key)
        if g is None:
            return
        if key in self._show_all:
            self._show_all.discard(key)
        else:
            self._show_all.add(key)
        lay = g["body"].layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._fill_group(lay, g["memos"], key)
        self._resize_timer.start()

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
                self._fill_group(g["body"].layout(), g["memos"], key)
        expanded = key in self._expanded
        g["hdr"].set_state(expanded, len(g["memos"]))
        g["body"].setVisible(expanded)
        if expanded:
            effects.fade_widget(g["body"], 0.0, 1.0, effects.NORMAL)
        self._resize_timer.start()

    def _make_row(self, memo: Memo) -> QWidget:
        row = QWidget()
        row.setObjectName("widgetRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(8)

        btn = QPushButton()
        btn.setObjectName("widgetDone")
        btn.setFixedSize(18, 18)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAccessibleName("标记完成")
        btn.setToolTip("标记完成")
        mid = memo.id
        btn.clicked.connect(lambda _c=False, b=btn, i=mid: self._on_row_done(b, i))
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)

        markers = []
        tip_parts = []
        if memo.due_ts:
            due_str = nlp_time.format_due(memo.due_ts)
            markers.append(due_str)
            tip_parts.append(due_str)
            if memo.remind_advance:
                tip_parts.append(f"提前{memo.remind_advance}分钟提醒")
        if memo.priority >= 1:
            markers.append(f"{db.PRIORITY_LABELS.get(memo.priority, '')}优先级")
            tip_parts.append(f"优先级{db.PRIORITY_LABELS.get(memo.priority, '')}")
        if memo.recur:
            markers.append(db.RECUR_LABELS.get(memo.recur, "重复"))
            tip_parts.append(f"重复{db.RECUR_LABELS.get(memo.recur, '')}")
        # label 对鼠标透明，tooltip 需挂到 row 才能触发
        row.setToolTip(
            memo.content + ("\n" + " · ".join(tip_parts) if tip_parts else "")
        )

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        label = QLabel(memo.content)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName("widgetItem")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 单行省略：宽度固定（面板定宽 300，扣除边距/圆圈/间距）
        label.setFixedWidth(300 - 16 * 2 - 18 - 8 - 24 - 8)
        fm = label.fontMetrics()
        label.setText(fm.elidedText(memo.content, Qt.TextElideMode.ElideRight, label.width()))
        text_col.addWidget(label)
        if markers:
            metadata = QLabel()
            metadata.setObjectName("widgetMeta")
            metadata.setTextFormat(Qt.TextFormat.PlainText)
            metadata.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            metadata.setFixedWidth(label.width())
            metadata.setText(metadata.fontMetrics().elidedText(
                " · ".join(markers), Qt.TextElideMode.ElideRight, metadata.width()
            ))
            text_col.addWidget(metadata)
        lay.addLayout(text_col, 1)

        # 置顶标记
        if memo.is_pinned:
            pin = QLabel()
            pin.setPixmap(ui_icon("pin", wcolor("pin"), 12).pixmap(12, 12))
            pin.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lay.addWidget(pin, 0, Qt.AlignmentFlag.AlignTop)

        return row

    def _on_row_done(self, btn: QPushButton, memo_id: int) -> None:
        if memo_id in self._pending_done:
            return
        self._pending_done.add(memo_id)
        btn.setProperty("done", "true")
        btn.setIcon(ui_icon("check", "#FFFFFF", 12))
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        # 先展示打勾，再真正完成（定时器挂在挂件上，行被重建也不会丢失这次点击）
        QTimer.singleShot(effects.NORMAL if effects.enabled() else 0, self, lambda: self._emit_done(memo_id))

    def _emit_done(self, memo_id: int) -> None:
        self._pending_done.discard(memo_id)
        # 等待期间若已在主界面/同步中完成或删除，不再发出（toggle 会把它反向取消完成）
        current = next((m for m in db.list_memos() if m.id == memo_id), None)
        if current is not None and not current.is_done:
            self.done_toggled.emit(memo_id)

    # ---------- 显示 / 位置 ----------

    def _move_to_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(
            geo.right() - self.width() - 24,
            geo.bottom() - 320,
        )

    def _relayout(self) -> None:
        """尺寸变化后的统一收口：滚动区限高 → 重排 → 钳回屏幕可用区域。"""
        # 面板原本完整在屏内（例如默认停在右下角）时，展开变高后仍完整推回屏内；
        # 用户主动把面板藏了一半到屏幕外时，保持其位置不弹回
        was_inside = self._fully_on_screen()
        self._update_scroll_bound()
        # 滚动区 sizeHint 依赖内容高度：内容增删后必须让布局系统重算。
        # updateGeometry 只投递 LayoutRequest（异步），这里同步冲刷，
        # 否则随后的 adjustSize 会用到旧的尺寸缓存。
        self.list_scroll.updateGeometry()
        QCoreApplication.sendPostedEvents(self, QEvent.Type.LayoutRequest)
        self.adjustSize()
        self._clamp_to_screen(strict=was_inside)

    def _current_screen(self):
        """面板当前所在屏幕：优先中心点所在屏，其次与面板相交面积最大的屏。

        面板允许部分移出屏幕，左上角可能落在屏幕外，不能只看 pos()。
        """
        frame = self.frameGeometry()
        screen = QGuiApplication.screenAt(frame.center())
        if screen is not None:
            return screen
        best, best_area = None, 0
        for s in QGuiApplication.screens():
            inter = s.availableGeometry().intersected(frame)
            area = inter.width() * inter.height()
            if area > best_area:
                best, best_area = s, area
        return best or QGuiApplication.primaryScreen()

    def _update_scroll_bound(self) -> None:
        """按当前所在屏幕的可用高度限制列表滚动区，保证面板不超出屏幕。"""
        screen = self._current_screen()
        if screen is None:
            return
        bound = min(_SCROLL_MAX, screen.availableGeometry().height() - _SCROLL_RESERVE)
        self.list_scroll.setMaximumHeight(max(_SCROLL_MIN, bound))

    def _fully_on_screen(self) -> bool:
        screen = self._current_screen()
        if screen is None:
            return True
        return screen.availableGeometry().contains(self.frameGeometry())

    def _clamp_to_screen(self, strict: bool = False) -> None:
        """保证面板仍可被抓回来：允许左/右/下方部分移出屏幕（"贴边藏一半"），
        但至少保留 _KEEP_VISIBLE 像素宽、标题栏高度在屏幕内，且顶部不越出屏幕上沿。

        strict=True 时完整钳回可用区域（面板原本就完整可见、只是因展开而变高）。"""
        screen = self._current_screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        keep_x = w if strict else min(_KEEP_VISIBLE, w)
        keep_y = h if strict else min(_KEEP_VISIBLE_Y, h)
        if strict and (w > geo.width() or h > geo.height()):
            keep_x, keep_y = min(w, geo.width()), min(h, geo.height())
        x = min(max(self.x(), geo.left() - w + keep_x), geo.right() + 1 - keep_x)
        y = min(max(self.y(), geo.top()), geo.bottom() + 1 - keep_y)
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ---------- 绘制 / 显隐 ----------

    def paintEvent(self, e):  # noqa: N802
        """Keep the original background translucency without fading the text."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paper = theme.is_paper()
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0), 6 if paper else 12, 6 if paper else 12
        )
        p.fillPath(path, QColor(*wcolor("panel")))
        p.setPen(QPen(QColor(*wcolor("edge")), 1.0))
        p.drawPath(path)
        if paper:
            # 一截和纸胶带，把纸条"贴"在桌面上
            tape = QRectF(self.width() / 2 - 46, 0, 92, 9)
            p.fillRect(tape, QColor(199, 167, 124, 150))
        p.end()

    def showEvent(self, e):  # noqa: N802
        effects.fade_window_in(self, effects.NORMAL)
        super().showEvent(e)
        # 主窗口最小化后挂件重新出现等场景：尺寸/位置重新收口到屏幕内
        self._relayout()

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
            # 松手后只做最低限度的收口：可以藏一半在屏幕外，但不会整个丢失
            self._clamp_to_screen()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.open_main.emit()
        super().mouseDoubleClickEvent(e)
