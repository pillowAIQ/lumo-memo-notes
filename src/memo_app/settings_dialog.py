"""设置页（主窗口内整页，仿编辑器设置）：常规 / 外观 / 快捷键 / 多端同步 / 关于。

侧栏左下角「设置」菜单直接进入对应分区；左侧分区导航，可随时返回笔记。
同步分区保留原有坚果云 WebDAV 引导逻辑（仿 Obsidian Sync）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import effects, theme
from .icons import mark_pixmap, ui_icon
from .remote_dir_dialog import RemoteDirDialog
from .sync.transport import WebDAVError

PAGES = (
    ("general", "常规", "toggle"),
    ("appearance", "外观", "palette"),
    ("hotkeys", "快捷键", "keyboard"),
    ("sync", "多端同步", "cloud"),
    ("about", "关于", "info"),
)


# ---------- 通用构件 ----------


class Switch(QAbstractButton):
    """可勾选的拨动开关（与 QCheckBox 相同的 setChecked/isChecked/toggled 接口）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(40, 22)
        self._t = 0.0
        self._instant = False
        self._fx = QObject(self)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        if self._instant:
            return
        effects.tween(self._fx, self._t, 1.0 if on else 0.0, effects.NORMAL, self._set_t)

    def _set_t(self, v: float) -> None:
        self._t = v
        self.update()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        # 初始化/程序设置时直接到位，不播放动画；用户点击仍走 toggled 动画
        self._instant = True
        try:
            super().setChecked(on)
        finally:
            self._instant = False
        self._t = 1.0 if on else 0.0
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 22)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._t
        track = QRectF(1, 1, 38, 20)
        path = QPainterPath()
        path.addRoundedRect(track, 10, 10)
        off = theme.qc("border_hover")
        p.fillPath(path, effects.mix(off, theme.c("accent"), t))
        if self.hasFocus():
            p.setPen(QPen(theme.qc("focus"), 1.5))
            p.drawPath(path)
        knob_x = 3 + 18 * t
        knob = QPainterPath()
        knob.addEllipse(QRectF(knob_x, 3, 16, 16))
        p.fillPath(knob, QColor("#E9EDEA") if theme.is_dark() else QColor("#FFFFFF"))
        if not self.isEnabled():
            p.fillPath(path, theme.qc("bg", 120))
        p.end()


class _Card(QWidget):
    """设置卡片：行与行之间自动插入分隔线（隐藏的行不会留下多余分隔线）。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("settingsCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._rows: list[tuple[QWidget, QWidget]] = []

    def add(self, row: QWidget) -> QWidget:
        line = QWidget()
        line.setObjectName("rowLine")
        line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        line.setFixedHeight(1)
        self._lay.addWidget(line)
        self._lay.addWidget(row)
        self._rows.append((line, row))
        row.installEventFilter(self)
        self._relayout()
        return row

    def _relayout(self) -> None:
        first = True
        for line, row in self._rows:
            visible = not row.isHidden()
            line.setVisible(visible and not first)
            first = first and not visible

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.HideToParent, QEvent.Type.ShowToParent):
            self._relayout()
        return False


def setting_row(title: str, desc: str = "", *controls: QWidget, stretch_control: bool = False) -> QWidget:
    row = QWidget()
    row.setObjectName("settingsRow")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(18, 14, 16, 14)
    lay.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(3)
    t = QLabel(title)
    t.setObjectName("rowTitle")
    text.addWidget(t)
    row.desc_label = None
    if desc:
        d = QLabel(desc)
        d.setObjectName("rowDesc")
        d.setWordWrap(True)
        text.addWidget(d)
        row.desc_label = d
    lay.addLayout(text, 1 if not stretch_control else 0)
    for c in controls:
        lay.addWidget(c, 1 if stretch_control else 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class _Page(QWidget):
    def __init__(self, title: str, desc: str) -> None:
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(44, 34, 44, 40)
        self.lay.setSpacing(0)
        t = QLabel(title)
        t.setObjectName("settingsTitle")
        d = QLabel(desc)
        d.setObjectName("settingsDesc")
        d.setWordWrap(True)
        self.lay.addWidget(t)
        self.lay.addSpacing(6)
        self.lay.addWidget(d)
        self.lay.addSpacing(22)

    def group(self, title: str = "") -> _Card:
        if title:
            label = QLabel(title)
            label.setObjectName("groupTitle")
            self.lay.addWidget(label)
            self.lay.addSpacing(8)
        card = _Card()
        self.lay.addWidget(card)
        self.lay.addSpacing(22)
        return card

    def finish(self) -> None:
        self.lay.addStretch(1)

    def set_compact(self, compact: bool) -> None:
        side = 22 if compact else 44
        self.lay.setContentsMargins(side, 24 if compact else 34, side, 32 if compact else 40)


def _field(widget: QWidget, width: int = 300) -> QWidget:
    # 宽窗口下给足输入宽度，窄窗口可收缩到 170，避免整页横向溢出
    widget.setMinimumWidth(170)
    widget.setMaximumWidth(width + 120)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return widget


# ---------- 常规 ----------


class GeneralPage(_Page):
    def __init__(self, ctl) -> None:
        super().__init__("常规", "桌面挂件与数据存放位置。")
        self.ctl = ctl
        card = self.group("桌面")
        self.widget_switch = Switch()
        self.widget_switch.setChecked(bool(getattr(ctl, "_widget_enabled", True)))
        self.widget_switch.setAccessibleName("自动显示桌面挂件")
        self.widget_switch.toggled.connect(self._on_widget)
        card.add(setting_row(
            "自动显示桌面挂件",
            "主窗口最小化或关闭到托盘后，在屏幕右下角显示未完成的待办。",
            self.widget_switch,
        ))
        card.add(setting_row(
            "关闭窗口",
            "点击关闭按钮会隐藏到系统托盘；从托盘菜单选择「退出」才会结束程序。",
        ))

        data = self.group("数据")
        from . import db
        self._data_dir = db.data_dir()
        open_btn = QPushButton("打开文件夹")
        open_btn.setIcon(ui_icon("external", theme.c("text_2"), 14))
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._data_dir))))
        data.add(setting_row("数据文件夹", f"{self._data_dir}\n本地 SQLite 存储；同步仅上传加密后的数据。", open_btn))
        self.finish()

    def _on_widget(self, on: bool) -> None:
        handler = getattr(self.ctl, "_on_widget_toggled", None)
        if handler is not None:
            handler(on)


# ---------- 外观 ----------


class ThemeCard(QAbstractButton):
    """皮肤预览卡：用对应配色画出迷你版主界面。"""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(124, 150)
        self.setMaximumSize(188, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(f"皮肤：{theme.palette(name)['label']}")
        self._hover = False

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):  # noqa: N802
        pal = theme.palette(self.name)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        outer = QPainterPath()
        outer.addRoundedRect(frame, 12, 12)
        p.fillPath(outer, theme.qc("surface"))
        selected = self.isChecked()
        ring = theme.qc("accent") if selected else (theme.qc("border_hover") if self._hover else theme.qc("border"))
        p.setPen(QPen(ring, 2.0 if selected else 1.0))
        p.drawPath(outer)

        shot = QRectF(10, 10, frame.width() - 17, 96)
        clip = QPainterPath()
        clip.addRoundedRect(shot, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.fillRect(shot, effects.mix(pal["content_a"], pal["content_b"], 0.5))
        p.fillRect(QRectF(shot.left(), shot.top(), shot.width(), 12), QColor(pal["titlebar"]))
        side = QRectF(shot.left(), shot.top() + 12, 46, shot.height() - 12)
        p.fillRect(side, QColor(pal["side"]))
        p.fillRect(QRectF(side.left() + 6, side.top() + 10, 34, 8), QColor(pal["accent_soft"]))
        for i in range(3):
            p.fillRect(QRectF(side.left() + 8, side.top() + 26 + i * 12, 26 - i * 4, 4), QColor(pal["faint"]))
        x0 = side.right() + 10
        p.fillRect(QRectF(x0, shot.top() + 22, max(16, min(54, shot.right() - x0 - 46)), 7), QColor(pal["text"]))
        for i in range(2):
            card = QPainterPath()
            card.addRoundedRect(QRectF(x0, shot.top() + 38 + i * 26, shot.right() - x0 - 10, 20), 4, 4)
            p.fillPath(card, QColor(pal["surface"]))
            p.setPen(QPen(QColor(pal["border"]), 1))
            p.drawPath(card)
            dot = QPainterPath()
            dot.addEllipse(QRectF(x0 + 5, shot.top() + 44 + i * 26, 8, 8))
            p.setPen(QPen(QColor(pal["accent"]), 1.4))
            p.drawPath(dot)
            p.fillRect(QRectF(x0 + 18, shot.top() + 46 + i * 26,
                              max(10, min(50 - i * 14, shot.right() - x0 - 34)), 4), QColor(pal["text_2"]))
        btn = QPainterPath()
        btn.addRoundedRect(QRectF(shot.right() - 34, shot.top() + 20, 24, 11), 4, 4)
        p.fillPath(btn, QColor(pal["accent"]))
        p.restore()

        p.setPen(theme.qc("text"))
        font = p.font()
        font.setPointSizeF(10)
        font.setBold(selected)
        p.setFont(font)
        p.drawText(QRectF(14, 112, frame.width() - 40, 28), Qt.AlignmentFlag.AlignVCenter, pal["label"])
        swatch = QPainterPath()
        swatch.addEllipse(QRectF(frame.right() - 28, 119, 14, 14))
        p.fillPath(swatch, QColor(pal["accent"]))
        if selected:
            p.setPen(QPen(QColor(pal["on_accent"]), 1.6,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            cx, cy = frame.right() - 21, 126
            p.drawLine(int(cx - 3.5), int(cy), int(cx - 1), int(cy + 2.5))
            p.drawLine(int(cx - 1), int(cy + 2.5), int(cx + 3.5), int(cy - 2.5))
        p.end()


class StyleCard(QAbstractButton):
    """风格预览卡：用该风格的推荐配色画出结构示意（三栏行式 / 日历页+细线）。"""

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(180, 176)
        self.setMaximumSize(300, 176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        spec = theme.STYLES[key]
        self.setAccessibleName(f"风格：{spec['label']}")
        self.setToolTip(spec["desc"])
        self._hover = False

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):  # noqa: N802
        spec = theme.STYLES[self.key]
        pal = theme.palette(spec["palette"])
        paper = self.key == "paper"
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        outer = QPainterPath()
        outer.addRoundedRect(frame, 12, 12)
        p.fillPath(outer, theme.qc("surface"))
        selected = self.isChecked()
        ring = theme.qc("accent") if selected else (theme.qc("border_hover") if self._hover else theme.qc("border"))
        p.setPen(QPen(ring, 2.0 if selected else 1.0))
        p.drawPath(outer)

        shot = QRectF(10, 10, frame.width() - 17, 112)
        clip = QPainterPath()
        clip.addRoundedRect(shot, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.fillRect(shot, QColor(pal["bg"]))
        side = QRectF(shot.left(), shot.top(), shot.width() * 0.24, shot.height())
        p.fillRect(side, QColor(pal["side"]))
        detail = QRectF(shot.right() - shot.width() * 0.3, shot.top() + 8, shot.width() * 0.3, shot.height())
        lst = QRectF(side.right() + (0 if paper else 6), shot.top() + (0 if paper else 8),
                     detail.left() - side.right() - (0 if paper else 6), shot.height())
        if paper:
            p.setPen(QPen(QColor(pal["line"]), 1))
            p.drawLine(int(side.right()), int(shot.top()), int(side.right()), int(shot.bottom()))
            # 日历页：大号日期 + 两行小字
            p.setPen(QColor(pal["text"]))
            font = p.font()
            font.setPixelSize(26)
            font.setFamilies(["Noto Serif SC", "STSong", "SimSun"])
            p.setFont(font)
            p.drawText(QRectF(side.left() + 8, side.top() + 8, side.width(), 30), Qt.AlignmentFlag.AlignLeft, "19")
            p.fillRect(QRectF(side.left() + 9, side.top() + 42, side.width() * 0.55, 3), QColor(pal["text_2"]))
            p.fillRect(QRectF(side.left() + 9, side.top() + 49, side.width() * 0.4, 3), QColor(pal["accent"]))
            for i in range(3):
                p.fillRect(QRectF(side.left() + 9, side.top() + 64 + i * 11, side.width() * 0.5, 3), QColor(pal["faint"]))
            x0 = lst.left() + 10
            p.fillRect(QRectF(x0, lst.top() + 10, 46, 7), QColor(pal["text"]))
            p.setPen(QPen(QColor(pal["text_2"]), 1))
            p.drawLine(int(x0), int(lst.top() + 30), int(lst.right() - 8), int(lst.top() + 30))
            for i in range(3):
                y = lst.top() + 42 + i * 22
                p.setPen(QPen(QColor(pal["text_2"]), 1.1))
                p.drawEllipse(QRectF(x0, y, 8, 8))
                p.fillRect(QRectF(x0 + 14, y + 2, lst.width() * (0.62 - i * 0.12), 4), QColor(pal["text"]))
                p.setPen(QPen(QColor(pal["line"]), 1, Qt.PenStyle.DotLine))
                p.drawLine(int(x0), int(y + 15), int(lst.right() - 8), int(y + 15))
            p.fillRect(QRectF(x0 - 6, lst.top() + 64, 2, 14), QColor(pal["accent"]))
        else:
            p.fillRect(QRectF(side.left() + 6, side.top() + 10, side.width() - 12, 9), QColor(pal["surface"]))
            for i in range(3):
                dot = QPainterPath()
                dot.addRoundedRect(QRectF(side.left() + 8, side.top() + 30 + i * 13, 5, 5), 1.5, 1.5)
                p.fillPath(dot, QColor(pal["cat_colors"][i]))
                p.fillRect(QRectF(side.left() + 17, side.top() + 31 + i * 13, side.width() * 0.45, 3), QColor(pal["faint"]))
            panel = QPainterPath()
            panel.addRoundedRect(lst.adjusted(0, 0, 0, 10), 6, 6)
            p.fillPath(panel, QColor(pal["content_a"]))
            x0 = lst.left() + 8
            box = QPainterPath()
            box.addRoundedRect(QRectF(x0, lst.top() + 8, lst.width() - 16, 13), 3, 3)
            p.fillPath(box, QColor(pal["surface"]))
            p.fillRect(QRectF(x0, lst.top() + 20, lst.width() - 16, 1.5), QColor(pal["accent"]))
            for i in range(3):
                y = lst.top() + 30 + i * 20
                row = QPainterPath()
                row.addRoundedRect(QRectF(x0 - 2, y, lst.width() - 12, 16), 4, 4)
                if i == 1:
                    p.fillPath(row, QColor(pal["selected"]))
                    p.fillRect(QRectF(x0 - 2, y + 4, 2, 8), QColor(pal["accent"]))
                p.setPen(QPen(QColor(pal["prio"] if i == 1 else pal["faint"]), 1.1))
                p.drawEllipse(QRectF(x0 + 3, y + 4, 8, 8))
                p.fillRect(QRectF(x0 + 16, y + 6, lst.width() * (0.6 - i * 0.1), 4), QColor(pal["text"]))
            p.fillRect(detail, QColor(pal["surface"]))
            p.setPen(QPen(QColor(pal["line"]), 1))
            p.drawLine(int(detail.left()), int(detail.top()), int(detail.left()), int(shot.bottom()))
            p.fillRect(QRectF(detail.left() + 8, detail.top() + 10, detail.width() * 0.6, 5), QColor(pal["text"]))
            for i in range(4):
                chip = QPainterPath()
                chip.addRoundedRect(QRectF(detail.left() + 8, detail.top() + 26 + i * 15, detail.width() * 0.7, 9), 2, 2)
                p.fillPath(chip, QColor(pal["accent_soft"] if i == 0 else pal["surface_alt"]))
        p.restore()

        p.setPen(theme.qc("text"))
        font = self.font()
        font.setPointSizeF(10.5)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(14, 128, frame.width() - 50, 22), Qt.AlignmentFlag.AlignVCenter, spec["label"])
        font.setBold(False)
        font.setPointSizeF(8.5)
        p.setFont(font)
        p.setPen(theme.qc("muted"))
        desc = p.fontMetrics().elidedText(spec["desc"], Qt.TextElideMode.ElideRight,
                                          int(frame.width() - 28))
        p.drawText(QRectF(14, 148, frame.width() - 28, 20), Qt.AlignmentFlag.AlignVCenter, desc)
        if selected:
            badge = QPainterPath()
            badge.addEllipse(QRectF(frame.right() - 30, 132, 16, 16))
            p.fillPath(badge, theme.qc("accent"))
            p.setPen(QPen(theme.qc("on_accent"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            cx, cy = frame.right() - 22, 140
            p.drawLine(int(cx - 4), int(cy), int(cx - 1), int(cy + 3))
            p.drawLine(int(cx - 1), int(cy + 3), int(cx + 4), int(cy - 3))
        p.end()


class AppearancePage(_Page):
    def __init__(self) -> None:
        super().__init__("外观", "风格决定布局与字体，皮肤决定配色，两者可以自由组合；立即生效，下次启动沿用。")
        style_label = QLabel("风格")
        style_label.setObjectName("groupTitle")
        self.lay.addWidget(style_label)
        self.lay.addSpacing(10)
        srow = QHBoxLayout()
        srow.setSpacing(14)
        self.style_cards = QButtonGroup(self)
        self.style_cards.setExclusive(True)
        for key in theme.STYLES:
            card = StyleCard(key)
            card.setChecked(key == theme.style_name())
            card.clicked.connect(lambda _c=False, k=key: self._pick_style(k))
            self.style_cards.addButton(card)
            srow.addWidget(card)
        srow.addStretch(1)
        self.lay.addLayout(srow)
        self.lay.addSpacing(8)
        shint = QLabel("切换风格会换上它的推荐配色；之后仍可在下方单独更换皮肤。")
        shint.setObjectName("rowDesc")
        shint.setWordWrap(True)
        self.lay.addWidget(shint)
        self.lay.addSpacing(22)

        label = QLabel("皮肤")
        label.setObjectName("groupTitle")
        self.lay.addWidget(label)
        self.lay.addSpacing(10)
        # 固定三列网格（3 + 2）：五张卡排成一行会在窄窗里撑出横向滚动
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        self.cards = QButtonGroup(self)
        self.cards.setExclusive(True)
        for i, name in enumerate(theme.PALETTES):
            card = ThemeCard(name)
            card.setChecked(name == theme.current_name())
            card.clicked.connect(lambda _c=False, n=name: theme.apply(n, persist=True))
            self.cards.addButton(card)
            grid.addWidget(card, i // 3, i % 3)
        grid.setColumnStretch(3, 1)
        self.lay.addLayout(grid)
        self.lay.addSpacing(14)
        hint = QLabel("云母蓝：清爽的 Windows 蓝 · 宣纸：暖纸与朱砂 · 松绿：温润的绿 · 墨夜：深色护眼 · 薰衣草：柔和的浅紫")
        hint.setObjectName("rowDesc")
        hint.setWordWrap(True)
        self.lay.addWidget(hint)
        self.finish()

    @staticmethod
    def _pick_style(key: str) -> None:
        if key != theme.style_name():
            theme.apply_style(key, persist=True)

    def sync_selection(self) -> None:
        for card in self.cards.buttons():
            card.setChecked(card.name == theme.current_name())
            card.update()
        for card in self.style_cards.buttons():
            card.setChecked(card.key == theme.style_name())
            card.update()


# ---------- 快捷键 ----------


class HotkeysPage(_Page):
    def __init__(self, ctl) -> None:
        super().__init__("快捷键", "在任何应用里一键呼出快捷记录；以及主界面的常用按键。")
        self.ctl = ctl
        self._recording = False
        card = self.group("全局")
        self.hotkey_edit = QLineEdit(ctl.hotkey_display())
        self.hotkey_edit.setPlaceholderText("点击录制，或手动输入如 Ctrl+Shift+K")
        self.hotkey_edit.setAccessibleName("快捷记录快捷键")
        self.hotkey_edit.setMinimumWidth(120)
        self.hotkey_edit.setMaximumWidth(200)
        self.record_btn = QPushButton("录制")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setCheckable(True)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setObjectName("primaryBtn")
        self.apply_btn.clicked.connect(self._on_apply)
        row = setting_row("呼出快捷记录", "需包含 Ctrl / Shift / Alt / Win 之一，再加一个普通键。",
                          self.hotkey_edit, self.record_btn, self.apply_btn)
        self.tip = row.desc_label
        card.add(row)

        keys = self.group("主界面")
        for title, combo in (
            ("搜索笔记", ("Ctrl", "F")),
            ("保存新记录", ("Enter",)),
            ("清空搜索 / 取消编辑 / 返回", ("Esc",)),
            ("删除选中的记录", ("Delete",)),
            ("编辑记录", ("双击",)),
            ("快捷记录中选择分类", ("/", "Tab")),
        ):
            caps = QWidget()
            cl = QHBoxLayout(caps)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(4)
            for key in combo:
                cap = QLabel(key)
                cap.setObjectName("keycap")
                cl.addWidget(cap)
            keys.add(setting_row(title, "", caps))
        self.finish()

        self.record_btn.toggled.connect(self._on_record_toggled)
        # 手动输入时自动退出录制，避免 _poll 覆盖正在输入的内容
        self.hotkey_edit.textEdited.connect(self._on_manual_edit)
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._poll)

    def _set_tip(self, text: str, error: bool = False) -> None:
        self.tip.setText(text)
        self.tip.setObjectName("statusErr" if error else "rowDesc")
        self.tip.style().unpolish(self.tip)
        self.tip.style().polish(self.tip)

    def _on_record_toggled(self, on: bool) -> None:
        self._recording = on
        self.record_btn.setText("录制中…" if on else "录制")
        if on:
            self.hotkey_edit.clear()
            self.hotkey_edit.setPlaceholderText("按下快捷键…")
            self._timer.start()
        else:
            self._timer.stop()
            self.hotkey_edit.setPlaceholderText("点击录制，或手动输入如 Ctrl+Shift+K")

    def _on_manual_edit(self, _text: str) -> None:
        if self._recording:
            self.record_btn.setChecked(False)

    def _poll(self) -> None:
        if not self._recording:
            return
        try:
            desc = self.ctl.describe_pressed()
        except Exception:
            return
        if desc:
            self.hotkey_edit.setText(desc)
            self.record_btn.setChecked(False)

    def _on_apply(self) -> None:
        if self.ctl.set_hotkey(self.hotkey_edit.text()):
            self.hotkey_edit.setText(self.ctl.hotkey_display())
            self._set_tip(f"已生效：{self.ctl.hotkey_display()}")
        else:
            self._set_tip("快捷键格式无法识别，请重新录制（需修饰键 + 普通键）", error=True)


# ---------- 多端同步（仿 Obsidian Sync：提醒 / 账号 / 同步库 / 冲突 / 状态） ----------


class SyncSettingsPage(_Page):
    def __init__(self, ctl) -> None:
        super().__init__("多端同步", "通过坚果云 WebDAV 在多台设备间同步，数据端到端加密。")
        self.ctl = ctl
        svc = ctl.sync

        # ── 状态 ────────────────────────────────────────
        banner = QWidget()
        banner.setObjectName("statusBanner")
        banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(12)
        self._banner_icon = QLabel()
        self._banner_icon.setPixmap(ui_icon("cloud", theme.c("accent"), 22).pixmap(22, 22))
        bl.addWidget(self._banner_icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        cap = QLabel("同步状态")
        cap.setObjectName("rowTitle")
        self.sync_status = QLabel(svc.status_text)
        self.sync_status.setObjectName("rowDesc")
        self.sync_status.setWordWrap(True)
        col.addWidget(cap)
        col.addWidget(self.sync_status)
        bl.addLayout(col, 1)
        self.lay.addWidget(banner)
        self.lay.addSpacing(12)

        # ── 备份提醒 ────────────────────────────────────
        warn = QLabel("注意：同步过程会修改或删除本地数据，建议在同步前备份重要文件。")
        warn.setObjectName("notice")
        warn.setWordWrap(True)
        self.lay.addWidget(warn)
        self.lay.addSpacing(22)

        switch_card = self.group()
        self.sync_enabled = Switch()
        self.sync_enabled.setChecked(svc.enabled)
        self.sync_enabled.setAccessibleName("启用多设备同步")
        switch_card.add(setting_row("启用多设备同步", "开启后每 30 分钟在后台自动同步，编辑后约 20 秒也会自动同步；更改此项后点底部「保存同步设置」生效。", self.sync_enabled))

        # ── 账号设置 ────────────────────────────────────
        account = self.group("坚果云账号")
        self.logout_btn = QPushButton("退出登录")
        self.logout_btn.setObjectName("dangerBtn")
        self.logout_btn.clicked.connect(self._on_logout)
        self.login_row = account.add(setting_row("已登录", "", self.logout_btn))
        self.login_label = self.login_row.findChild(QLabel, "rowTitle")

        self.sync_url = _field(QLineEdit(svc.base_url))
        self.sync_url.setPlaceholderText("https://dav.jianguoyun.com/dav/")
        self.url_row = account.add(setting_row("服务器地址", "坚果云默认地址即可。", self.sync_url))

        self.sync_account = _field(QLineEdit())
        self.sync_account.setPlaceholderText("坚果云账号（邮箱）")
        self.account_row = account.add(setting_row("账号", "", self.sync_account))

        self.sync_app_pw = _field(QLineEdit())
        self.sync_app_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.sync_app_pw.setPlaceholderText("第三方应用密码（留空则沿用已保存的）")
        self.password_row = account.add(setting_row(
            "应用密码", "在坚果云网页「安全选项 → 第三方应用管理」中生成。", self.sync_app_pw))

        self.test_btn = QPushButton("检查连接")
        self.test_btn.clicked.connect(self._on_test_conn)
        check_row = account.add(setting_row("WebDAV 连接", "检查账号与应用密码是否可用。", self.test_btn))
        self.conn_result = check_row.desc_label  # 行描述位显示检查结果

        # ── 同步库 ──────────────────────────────────────
        vault = self.group("同步库")
        self.remote_dir_edit = QLineEdit("/" + svc.remote_dir if svc.remote_dir else "/")
        self.remote_dir_edit.setPlaceholderText("/我的坚果云/memo")
        self.remote_dir_edit.setMinimumWidth(130)
        self.remote_dir_edit.setMaximumWidth(240)
        self.browse_btn = QPushButton("浏览…")
        self.browse_btn.clicked.connect(self._on_browse)
        vault.add(setting_row("远程目录", "另一台设备选择相同目录即可加入。", self.remote_dir_edit, self.browse_btn))

        self.vault_pw = _field(QLineEdit())
        self.vault_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.vault_pw.setPlaceholderText("至少 6 位，两台设备必须相同")
        self.vault_password_row = vault.add(setting_row(
            "同步库密码", "端到端加密密钥，务必牢记——遗忘后无法找回加密数据。", self.vault_pw))

        self.setup_btn = QPushButton("初始化同步库")
        self.setup_btn.setObjectName("primaryBtn")
        self.setup_btn.setToolTip("远程目录已有同步库则加入；没有则创建新库")
        self.setup_btn.clicked.connect(self._on_setup)
        self.setup_row = vault.add(setting_row(
            "初始化", "远程已有库会自动加入，没有会自动创建。", self.setup_btn))
        self.setup_hint = self.setup_row.desc_label

        # 库就绪信息（已绑定时显示远程目录说明）
        self.vault_box = QWidget()
        vbl = QVBoxLayout(self.vault_box)
        vbl.setContentsMargins(18, 12, 18, 14)
        self.vault_info = QLabel("")
        self.vault_info.setObjectName("rowDesc")
        self.vault_info.setWordWrap(True)
        vbl.addWidget(self.vault_info)
        self.vault_box.hide()
        vault.add(self.vault_box)

        # ── 冲突 ────────────────────────────────────────
        conflict = self.group("冲突处理")
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("后写覆盖（推荐）", "lww")
        self.conflict_combo.addItem("保留冲突副本", "keep")
        self.conflict_combo.setMinimumWidth(150)
        self.conflict_combo.setMaximumWidth(200)
        idx = self.conflict_combo.findData(svc.conflict_mode)
        if idx >= 0:
            self.conflict_combo.setCurrentIndex(idx)
        conflict.add(setting_row(
            "冲突解决策略",
            "两台设备同时修改同一条记录时的处理方式；手动「立即同步」时也可以临时选择。",
            self.conflict_combo,
        ))

        actions = QHBoxLayout()
        self.save_hint = QLabel("")
        self.save_hint.setObjectName("rowDesc")
        actions.addWidget(self.save_hint, 1)
        self.save_btn = QPushButton("保存同步设置")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self._on_save)
        actions.addWidget(self.save_btn)
        self.lay.addLayout(actions)
        self.finish()

        svc.status_changed.connect(self._on_status_text)
        svc.conn_tested.connect(self._on_conn_tested)
        svc.vault_ready.connect(self._on_vault_ready)

        self._vault_id = ""
        self._setup_pending = False  # 初始化 job 进行中
        # 异步任务期间锁定的输入控件
        self._sync_inputs = [
            self.sync_enabled, self.sync_url, self.sync_account,
            self.sync_app_pw, self.test_btn, self.vault_pw,
            self.remote_dir_edit, self.browse_btn,
            self.conflict_combo, self.logout_btn,
        ]

        cur_vid = svc.vault_id()
        if cur_vid:
            self._show_vault_ready(cur_vid, announce=False)
        self._refresh_login_state()

    # ---------- 同步引导动作 ----------

    def _set_sync_pending(self, on: bool) -> None:
        """初始化进行中锁定同步输入，防止表单与实际 job 不一致。"""
        self._setup_pending = on
        for w in self._sync_inputs:
            w.setEnabled(not on)
        if on:
            self.setup_btn.setEnabled(False)
            self.setup_btn.setText("正在连接坚果云…")
        elif not self._vault_id:
            self.setup_btn.setEnabled(True)
            self.setup_btn.setText("初始化同步库")

    def _set_conn_result(self, text: str, ok: bool | None = None) -> None:
        self.conn_result.setText(text or "检查账号与应用密码是否可用。")
        self.conn_result.setObjectName("rowDesc" if ok is None else ("statusOk" if ok else "statusErr"))
        self.conn_result.style().unpolish(self.conn_result)
        self.conn_result.style().polish(self.conn_result)

    def _on_test_conn(self) -> None:
        for w in (self.test_btn, self.sync_url, self.sync_account, self.sync_app_pw, self.logout_btn):
            w.setEnabled(False)
        self.test_btn.setText("检查中…")
        self._set_conn_result("")
        self.ctl.sync.test_connection(
            self.sync_account.text().strip(),
            self.sync_app_pw.text(),
            self.sync_url.text().strip(),
        )

    def _on_conn_tested(self, ok: bool, msg: str) -> None:
        # pending 时按钮虽 disabled 但文本也要复位，避免滞留"检查中…"
        if not self._setup_pending:
            for w in (self.test_btn, self.sync_url, self.sync_account, self.sync_app_pw, self.logout_btn):
                w.setEnabled(True)
        if ok:
            self.sync_app_pw.clear()
        self._refresh_login_state()
        self._set_conn_result(("✓ " if ok else "✗ ") + msg, ok)

    def _on_status_text(self, text: str) -> None:
        self.sync_status.setText(text)
        # 初始化 job 失败（网络/凭据）：解锁输入允许重试
        failed = (
            text.startswith("同步失败")
            or text.startswith("错误")
            or "不可用" in text
        )
        if failed and self._setup_pending:
            self._set_sync_pending(False)

    def _refresh_login_state(self) -> None:
        """按凭据管理器状态刷新"已登录"行（仿 Obsidian 账号区）。"""
        from .sync import credentials
        try:
            acc, pw = credentials.load_credentials()
        except credentials.CredentialError:
            acc = pw = None
        logged_in = bool(acc and pw)
        for w in (self.url_row, self.account_row, self.password_row):
            w.setVisible(not logged_in)
        self.test_btn.setText("检查连接" if logged_in else "登录并保存")
        if logged_in:
            self.login_label.setText(f"已登录  ·  {acc}")
            self.login_row.show()
            if not self.sync_account.text().strip():
                self.sync_account.setText(acc)
        else:
            self.login_row.hide()

    def _on_logout(self) -> None:
        self.ctl.sync.logout()
        self.sync_app_pw.clear()
        self.sync_enabled.setChecked(self.ctl.sync.enabled)
        self._refresh_login_state()
        self.sync_status.setText(self.ctl.sync.status_text)

    def _on_browse(self) -> None:
        """打开远程目录选择器；需要账号凭据（字段或已存）。"""
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        if not app_pw:
            from .sync import credentials
            try:
                acc, pw = credentials.load_credentials()
            except Exception:
                acc = pw = None
            if pw and acc and (
                not account or acc.casefold() == account.casefold()
            ):
                account = account or acc
                app_pw = pw
        if not account or not app_pw:
            self.sync_status.setText("错误：请先填写账号和应用密码")
            return
        try:
            dlg = RemoteDirDialog(
                self.sync_url.text().strip()
                or "https://dav.jianguoyun.com/dav/",
                account,
                app_pw,
                self.remote_dir_edit.text().strip("/"),
                self,
                cache=self.ctl.sync.directory_cache,
            )
        except WebDAVError as e:
            # 构造时同步校验 base_url/凭据格式，非法输入直接报错
            self.sync_status.setText(f"错误：{e}")
            return
        if dlg.exec() == QDialog.DialogCode.Accepted:
            rd = dlg.result_dir()
            self.remote_dir_edit.setText("/" + rd if rd else "/")

    def _on_setup(self) -> None:
        """初始化同步库：远端有 vault.json 自动加入，没有自动创建。"""
        svc = self.ctl.sync
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        vault_pw = self.vault_pw.text()
        if not account:
            self.sync_status.setText("错误：请填写坚果云账号")
            return
        # 应用密码留空仅当"已存凭据属于同一账号"才可沿用；
        # 首次使用或账号变更必须重填，否则 save_config 不落凭据
        # 但 enabled 已持久化，会留下"已启用却无凭据"的半状态
        if not app_pw:
            from .sync import credentials
            try:
                old_acc, old_pw = credentials.load_credentials()
            except Exception:
                old_acc = old_pw = None
            if not (
                old_pw and old_acc
                and old_acc.casefold() == account.casefold()
            ):
                self.sync_status.setText(
                    "错误：请填写应用密码（首次使用或账号变更时必填）"
                )
                return
        if not vault_pw:
            self.sync_status.setText(
                "错误：请填写同步库密码（加密密钥，两台设备需相同）"
            )
            return
        if len(vault_pw) < 6:
            self.sync_status.setText("错误：同步库密码至少 6 位")
            return
        if svc.vault_id():
            self.sync_status.setText("本机已绑定同步库，无需重复初始化")
            return
        # 先保存配置（启用同步+凭据+远程目录+冲突策略），再排队初始化
        if not svc.save_config(
            enabled=True,
            base_url=self.sync_url.text().strip()
            or "https://dav.jianguoyun.com/dav/",
            account=account,
            app_password=app_pw,
            remote_dir=self.remote_dir_edit.text(),
            conflict_mode=self.conflict_combo.currentData(),
        ):
            return
        self.sync_enabled.setChecked(True)
        if not svc.queue_setup(vault_pw):
            return  # 凭据缺失，状态栏已提示；不进入 pending
        self._set_sync_pending(True)
        self.sync_status.setText("正在连接坚果云并初始化同步库，请稍候…")

    def _show_vault_ready(self, vid: str, announce: bool = True) -> None:
        self._vault_id = vid
        rd = "/" + self.ctl.sync.remote_dir if self.ctl.sync.remote_dir else "/"
        self.vault_info.setText(
            f"同步库已绑定 · 远程目录：{rd}\n"
            "数据会同步到坚果云该目录（可在坚果云客户端/网页端看到），"
            "另一台设备选相同远程目录 + 相同同步库密码即可加入。"
        )
        self.vault_box.show()
        self.vault_password_row.hide()
        self.vault_pw.clear()
        self.setup_hint.setText("本机已加入同步库。")
        self.setup_btn.setEnabled(False)
        self.setup_btn.setText("已绑定同步库 ✓")
        if announce:
            self.sync_status.setText("同步库已就绪，开始首次同步…")

    def _on_vault_ready(self, vid: str) -> None:
        self._set_sync_pending(False)
        self._show_vault_ready(str(vid))

    def _on_save(self) -> None:
        if self._save_sync():
            self.save_hint.setText("已保存")
            QTimer.singleShot(2500, self.save_hint, lambda: self.save_hint.setText(""))

    def _save_sync(self) -> bool:
        """保存同步开关/地址/账号/目录/策略。

        建库/加入由「初始化同步库」按钮单独触发（_on_setup），
        此处只持久化非密配置；已绑定库时顺带触发一次同步。
        """
        svc = self.ctl.sync
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        enabled = self.sync_enabled.isChecked()

        if enabled and not account:
            self.sync_status.setText("错误：请填写坚果云账号")
            return False
        # 与 _on_setup 同理：应用密码留空仅当已存凭据属于同一账号
        if enabled and not app_pw:
            from .sync import credentials
            try:
                old_acc, old_pw = credentials.load_credentials()
            except Exception:
                old_acc = old_pw = None
            if not (
                old_pw and old_acc
                and old_acc.casefold() == account.casefold()
            ):
                self.sync_status.setText(
                    "错误：请填写应用密码（首次使用或账号变更时必填）"
                )
                return False

        if not svc.save_config(
            enabled=enabled,
            base_url=self.sync_url.text().strip()
            or "https://dav.jianguoyun.com/dav/",
            account=account,
            app_password=app_pw,
            remote_dir=self.remote_dir_edit.text(),
            conflict_mode=self.conflict_combo.currentData(),
        ):
            return False  # 凭据保存失败，状态栏已提示
        if enabled and svc.vault_id():
            svc.sync_now()
        return True


# ---------- 关于 ----------


class AboutPage(_Page):
    def __init__(self) -> None:
        super().__init__("关于", "")
        hero = QHBoxLayout()
        hero.setSpacing(18)
        mark = QLabel()
        mark.setPixmap(mark_pixmap(72))
        hero.addWidget(mark)
        col = QVBoxLayout()
        col.setSpacing(4)
        name = QLabel(f"{theme.APP_NAME}  ·  {theme.APP_NAME_EN}")
        name.setObjectName("aboutName")
        ver = QLabel(f"版本 {theme.APP_VERSION}")
        ver.setObjectName("rowDesc")
        tag = QLabel("轻量、本地优先的随手记与待办。")
        tag.setObjectName("settingsDesc")
        col.addStretch(1)
        col.addWidget(name)
        col.addWidget(tag)
        col.addWidget(ver)
        col.addStretch(1)
        hero.addLayout(col, 1)
        self.lay.addLayout(hero)
        self.lay.addSpacing(26)
        from .updater import UpdatePanel
        updates = self.group("软件更新")
        updates.layout().addWidget(UpdatePanel())
        card = self.group("特性")
        for title, desc in (
            ("全局快捷记录", "任何应用中呼出输入框，回车即存，支持「明天9点」等自然语言提醒。"),
            ("桌面挂件", "主窗口收起后，在桌面一角查看并勾选待办。"),
            ("端到端加密同步", "经坚果云 WebDAV 在多台设备间同步，密钥只保存在你的设备上。"),
        ):
            card.add(setting_row(title, desc))
        self.finish()


# ---------- 设置整页 ----------


class SettingsView(QWidget):
    """主窗口内的设置整页：左侧分区导航 + 右侧可滚动内容。"""

    back_requested = Signal()

    def __init__(self, ctl, page: str = "general", parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav = QWidget()
        nav.setObjectName("settingsNav")
        nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav.setFixedWidth(214)
        self.nav = nav
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(14, 16, 14, 16)
        nl.setSpacing(4)
        self.back_btn = QPushButton(" 返回笔记")
        self.back_btn.setObjectName("backBtn")
        self.back_btn.setToolTip("返回 (Esc)")
        self.back_btn.clicked.connect(self.back_requested.emit)
        nl.addWidget(self.back_btn)
        nl.addSpacing(10)
        heading = QLabel("设置")
        heading.setObjectName("settingsHeading")
        heading.setContentsMargins(12, 0, 0, 0)
        nl.addWidget(heading)
        nl.addSpacing(10)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label, icon in PAGES:
            page_widget = self._make_page(key)
            if page_widget is None:
                continue
            self.pages[key] = page_widget
            self.stack.addWidget(self._scrolled(page_widget))
            btn = QPushButton(f"  {label}")
            btn.setObjectName("settingsNavItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            group.addButton(btn)
            self.nav_buttons[key] = btn
            nl.addWidget(btn)
        nl.addStretch(1)
        ver = QLabel(f"{theme.APP_NAME} {theme.APP_NAME_EN} {theme.APP_VERSION}")
        ver.setObjectName("rowDesc")
        ver.setContentsMargins(12, 0, 0, 0)
        nl.addWidget(ver)
        root.addWidget(nav)

        root.addWidget(self.stack, 1)

        self._retheme()
        theme.notifier.changed.connect(self._retheme)
        self.show_page(page if page in self.pages else "general")

    @staticmethod
    def _scrolled(page: QWidget) -> QScrollArea:
        """每个分区独立滚动：短页面不会被最长的同步页撑出滚动条。"""
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("settingsBody")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        page.setMaximumWidth(780)
        bl.addWidget(page, 1)
        bl.addStretch(0)
        scroll.setWidget(body)
        page.scroll_area = scroll
        return scroll

    @property
    def scroll(self) -> QScrollArea:
        return self.stack.currentWidget()

    def _make_page(self, key: str) -> QWidget | None:
        if key == "general":
            return GeneralPage(self.ctl)
        if key == "appearance":
            return AppearancePage()
        if key == "hotkeys":
            return HotkeysPage(self.ctl) if hasattr(self.ctl, "hotkey_display") else None
        if key == "sync":
            return SyncSettingsPage(self.ctl) if getattr(self.ctl, "sync", None) is not None else None
        return AboutPage()

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(page.scroll_area)
        page.scroll_area.verticalScrollBar().setValue(0)
        effects.fade_widget(page, 0.0, 1.0, effects.FAST + 40)

    def current_page(self) -> str:
        widget = self.stack.currentWidget()
        return next((k for k, w in self.pages.items() if w.scroll_area is widget), "general")

    def _retheme(self, *_args) -> None:
        self.back_btn.setIcon(ui_icon("back", theme.c("muted"), 14))
        for key, _label, icon in PAGES:
            btn = self.nav_buttons.get(key)
            if btn is not None:
                btn.setIcon(ui_icon(icon, theme.c("text_2"), 16))
        appearance = self.pages.get("appearance")
        if isinstance(appearance, AppearancePage):
            appearance.sync_selection()
        sync = self.pages.get("sync")
        if isinstance(sync, SyncSettingsPage):
            sync._banner_icon.setPixmap(ui_icon("cloud", theme.c("accent"), 22).pixmap(22, 22))
        for w in self.findChildren(QWidget):
            if isinstance(w, (Switch, ThemeCard, StyleCard)):
                w.update()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        compact = self.width() < 760
        self.nav.setFixedWidth(168 if compact else 214)
        for page in self.pages.values():
            page.set_compact(compact)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            return
        super().keyPressEvent(e)
