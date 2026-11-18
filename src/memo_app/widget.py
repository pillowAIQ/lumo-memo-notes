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
# 面板距屏幕可用区域边缘的最小留白
_EDGE = 8


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
        self.arrow.setPixmap(ui_icon("chevron_down" if expanded else "chevron_right", "#D3DCCB", 12).pixmap(12, 12))
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
        self.arrow.setPixmap(ui_icon("chevron_down" if expanded else "chevron_right", "#D3DCCB", 12).pixmap(12, 12))
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
            QLabel#widgetTitle { font-size: 14px; font-weight: bold; color: #F4F6EF; }
            QLabel#widgetCount { font-size: 11px; color: #D3DCCB; }
            QLabel#widgetItem { font-size: 12px; color: #F0F4EB; }
            QLabel#widgetMeta { font-size: 11px; color: #D3DCCB; }
            QLabel#widgetHint { font-size: 10px; color: #D3DCCB; }
            QLabel#widgetGroupName { font-size: 12px; font-weight: bold; color: #E2EBD9; }
            QLabel#widgetGroupArrow { font-size: 9px; color: #D3DCCB; }
            QLabel#widgetGroupCount { font-size: 10px; color: #D3DCCB; }
            GroupHeader { background: transparent; border: 1px solid transparent; border-radius: 6px; }
            GroupHeader:hover { background: rgba(209, 224, 196, 18); }
            GroupHeader:focus { background: rgba(209, 224, 196, 24); border-color: #9DB591; }
            QWidget#widgetRow { background: transparent; border-radius: 6px; }
            QWidget#widgetRow:hover { background: rgba(209, 224, 196, 16); }
            QPushButton#widgetBtn {
                border: none; background: transparent; color: #D3DCCB;
                border-radius: 5px; padding: 0;
            }
            QPushButton#widgetBtn:hover, QPushButton#widgetBtn:focus { background: rgba(209, 224, 196, 35); }
            QPushButton#widgetBtn:pressed { background: rgba(209, 224, 196, 60); }
            QPushButton#widgetDone {
                border: 1.5px solid #BFCFB2;
                border-radius: 9px;
                background: transparent; color: transparent;
                font-size: 10px; font-weight: bold; padding: 0;
            }
            QPushButton#widgetDone:hover, QPushButton#widgetDone:focus {
                border-color: #A7CE99; background: rgba(145, 186, 134, 35);
            }
            QPushButton#widgetDone[done="true"] {
                background: @ACCENT@; border-color: @ACCENT@; color: white;
            }
            QPushButton#widgetMore {
                border: 1px solid transparent; background: transparent;
                color: @MORE_TEXT@; font-size: 11px; border-radius: 6px;
                padding: 4px 10px; text-align: left;
            }
            QPushButton#widgetMore:hover, QPushButton#widgetMore:focus {
                background: @MORE_HOVER@; color: @MORE_HOVER_TEXT@;
            }
            QPushButton#widgetMore:pressed { background: @MORE_PRESS@; }
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
        # 挂件始终是深色半透明面板；仅勾选色跟随皮肤强调色（深色皮肤用其深色版本保证白勾对比）
        accent = theme.c("accent_press" if theme.is_dark() else "accent")
        self.setStyleSheet(
            self._qss.replace("@ACCENT@", accent)
            .replace("@MORE_TEXT@", theme.c("widget_more_text"))
            .replace("@MORE_HOVER_TEXT@", theme.c("widget_more_hover_text"))
            .replace("@MORE_HOVER@", theme.c("widget_more_hover"))
            .replace("@MORE_PRESS@", theme.c("widget_more_press"))
        )

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
        self.min_btn.setIcon(ui_icon("minus", "#D3DCCB", 14))
        self.min_btn.setObjectName("widgetBtn")
        self.min_btn.setFixedSize(22, 22)
        self.min_btn.setAccessibleName("最小化桌面挂件")
        self.min_btn.setToolTip("最小化为标题栏")
        self.hide_btn = QPushButton()
        self.hide_btn.setIcon(ui_icon("close", "#D3DCCB", 14))
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
        self.min_btn.setIcon(ui_icon(
            "restore_window" if self._minimized else "minus", "#D3DCCB", 14
        ))
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

        def group_key(m: Memo) -> str:
            cids = cat_map.get(m.id, set()) & cats.keys()
            return str(sorted(cids)[0]) if cids else "0"

        groups: dict[str, list[Memo]] = {}
        for m in todos:
            groups.setdefault(group_key(m), []).append(m)
        ordered = sorted(groups.keys(), key=lambda k: (k == "0", int(k)))

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
            pin.setPixmap(ui_icon("pin", "#E8C997", 12).pixmap(12, 12))
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
        self._update_scroll_bound()
        # 滚动区 sizeHint 依赖内容高度：内容增删后必须让布局系统重算。
        # updateGeometry 只投递 LayoutRequest（异步），这里同步冲刷，
        # 否则随后的 adjustSize 会用到旧的尺寸缓存。
        self.list_scroll.updateGeometry()
        QCoreApplication.sendPostedEvents(self, QEvent.Type.LayoutRequest)
        self.adjustSize()
        self._clamp_to_screen()

    def _update_scroll_bound(self) -> None:
        """按当前所在屏幕的可用高度限制列表滚动区，保证面板不超出屏幕。"""
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        bound = min(_SCROLL_MAX, screen.availableGeometry().height() - _SCROLL_RESERVE)
        self.list_scroll.setMaximumHeight(max(_SCROLL_MIN, bound))

    def _clamp_to_screen(self) -> None:
        """把面板矩形钳回所在屏幕的可用区域内（拖动/展开/还原后调用）。"""
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if w + 2 * _EDGE <= geo.width():
            x = min(max(self.x(), geo.left() + _EDGE), geo.right() - w - _EDGE)
        else:
            x = geo.left()
        if h + 2 * _EDGE <= geo.height():
            y = min(max(self.y(), geo.top() + _EDGE), geo.bottom() - h - _EDGE)
        else:
            y = geo.top()
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ---------- 绘制 / 显隐 ----------

    def paintEvent(self, e):  # noqa: N802
        """Keep the original background translucency without fading the text."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0), 16, 16
        )
        p.fillPath(path, QColor(38, 38, 44, 190))
        p.setPen(QPen(QColor(222, 232, 216, 60), 1.0))
        p.drawPath(path)
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
            # 拖到屏幕边缘附近松手：钳回可用区域，避免面板被拖出可视范围
            self._clamp_to_screen()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.open_main.emit()
        super().mouseDoubleClickEvent(e)
