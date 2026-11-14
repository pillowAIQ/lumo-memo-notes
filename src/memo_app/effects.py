"""Restrained motion helpers shared by the desktop UI.

All transitions stay within 120-240 ms, animate only opacity / position /
paint values (never layout sizes), and are skipped entirely when Windows
"Show animations" is turned off.

Lifetime rule: animations are long-lived children of the object they animate
and are reused. Never keep a Python reference to an animation started with
``DeleteWhenStopped`` -- PySide cannot see that deletion, and touching the
stale wrapper later is an access violation, not a RuntimeError.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QWidget,
)

from . import theme
from .icons import ui_icon

FAST, NORMAL, SLOW = 120, 180, 240


@lru_cache(maxsize=1)
def enabled() -> bool:
    """Follow the Windows "Show animations in Windows" accessibility switch."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        value = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:
        pass
    return True


def mix(a: QColor | str, b: QColor | str, t: float) -> QColor:
    a, b = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def tween(owner: QObject, start: float, end: float, ms: int,
          on_value: Callable[[float], None],
          curve: QEasingCurve.Type = QEasingCurve.Type.OutCubic) -> None:
    """Animate a float for custom-painted widgets (one reusable animation per owner)."""
    anim = getattr(owner, "_fx_tween", None)
    if anim is not None:
        anim.stop()
    if not enabled() or ms <= 0 or start == end:
        on_value(end)
        return
    if anim is None:
        anim = QVariantAnimation(owner)
        anim.valueChanged.connect(lambda v: owner._fx_on_value(float(v)))
        owner._fx_tween = anim
    owner._fx_on_value = on_value
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(ms)
    anim.setEasingCurve(curve)
    anim.start()


def _window_anim(win: QWidget, attr: str, prop: bytes) -> QPropertyAnimation:
    anim = getattr(win, attr, None)
    if anim is None:
        anim = QPropertyAnimation(win, prop, win)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        setattr(win, attr, anim)
    anim.stop()
    return anim


def fade_window_in(win: QWidget, ms: int = NORMAL, slide: int = 0) -> None:
    """Fade (and optionally drop in) a top-level window. Call right before/while showing."""
    fade = _window_anim(win, "_fx_fade", b"windowOpacity")
    if not enabled():
        win.setWindowOpacity(1.0)
        return
    win.setWindowOpacity(0.0)
    fade.setDuration(ms)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.start()
    if slide:
        move = _window_anim(win, "_fx_move", b"pos")
        end = win.pos()
        move.setDuration(ms + 40)
        move.setStartValue(end - QPoint(0, slide))
        move.setEndValue(end)
        move.start()


def fade_widget(w: QWidget, start: float, end: float, ms: int = NORMAL,
                then: Callable[[], None] | None = None) -> None:
    """Opacity transition for a child widget.

    The opacity effect is removed once fully visible so text keeps ClearType
    rendering. ``then`` is queued so callers may safely rebuild/delete ``w``;
    it is dropped if ``w`` is destroyed first (its owner already refreshed).
    """
    if not enabled():
        if then is not None:
            then()
        return
    effect = QGraphicsOpacityEffect(w)
    effect.setOpacity(start)
    w.setGraphicsEffect(effect)  # replaces (and deletes) any previous effect + its animation
    anim = QPropertyAnimation(effect, b"opacity", effect)
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic if end > start else QEasingCurve.Type.InCubic)

    def finished() -> None:
        if end >= 1.0:
            # Identity check only (no C++ access): skip if a newer effect replaced ours.
            QTimer.singleShot(0, w, lambda: w.graphicsEffect() is effect and w.setGraphicsEffect(None))
        if then is not None:
            QTimer.singleShot(0, w, then)

    anim.finished.connect(finished)
    anim.start()


class _IconSwap(QObject):
    """Swap a button icon while hovered/focused (no layout shift, no scaling)."""

    def __init__(self, button: QAbstractButton, normal: QIcon, hover: QIcon) -> None:
        super().__init__(button)
        self.normal, self.hover = normal, hover
        button.installEventFilter(self)
        self.sync(button)

    def set_icons(self, button: QAbstractButton, normal: QIcon, hover: QIcon | None = None) -> None:
        self.normal = normal
        if hover is not None:
            self.hover = hover
        self.sync(button)

    def sync(self, button: QAbstractButton, hovered: bool | None = None) -> None:
        if hovered is None:
            hovered = button.underMouse()
        active = button.isEnabled() and (hovered or button.hasFocus())
        button.setIcon(self.hover if active else self.normal)

    def eventFilter(self, obj, e):  # noqa: N802
        t = e.type()
        if t == QEvent.Type.Enter:
            self.sync(obj, True)
        elif t == QEvent.Type.Leave:
            self.sync(obj, False)
        elif t in (QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.EnabledChange):
            self.sync(obj)
        return False


def hover_icon(button: QAbstractButton, normal: QIcon, hover: QIcon) -> _IconSwap:
    swap = getattr(button, "_fx_icons", None)
    if swap is None:
        swap = _IconSwap(button, normal, hover)
        button._fx_icons = swap
    else:
        swap.set_icons(button, normal, hover)
    return swap


class Toast(QWidget):
    """Bottom-centred transient message with an optional action (e.g. 撤销)."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setObjectName("toastIcon")
        self.label = QLabel()
        self.label.setObjectName("toastText")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.action = QPushButton()
        self.action.setObjectName("toastAction")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(self._on_action)
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        lay.addWidget(self.action)
        self._callback: Callable[[], None] | None = None
        self._closing = False
        # Persistent effect + animations (see module docstring on lifetimes).
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.finished.connect(self._on_fade_finished)
        self._move = QPropertyAnimation(self, b"pos", self)
        self._move.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        host.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, icon: str = "check", action: str | None = None,
                     callback: Callable[[], None] | None = None, ms: int = 4000) -> None:
        self._closing = False
        self._fade.stop()
        self._move.stop()
        self._callback = callback
        self.icon.setPixmap(ui_icon(icon, theme.c("toast_accent"), 16).pixmap(16, 16))
        self.label.setText(text)
        has_action = bool(action and callback)
        self.action.setText(action or "")
        self.action.setVisible(has_action)
        self.layout().setContentsMargins(14, 8, 8 if has_action else 16, 8)
        self.adjustSize()
        target = self._target_pos()
        self.raise_()
        self.show()
        self._timer.start(ms)
        if not enabled():
            self._effect.setOpacity(1.0)
            self.move(target)
            return
        self._fade.setDuration(NORMAL)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._move.setDuration(SLOW)
        self._move.setStartValue(target + QPoint(0, 10))
        self._move.setEndValue(target)
        self._fade.start()
        self._move.start()

    def dismiss(self) -> None:
        self._timer.stop()
        self._callback = None
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._move.stop()
        self._fade.stop()
        if not enabled():
            self.hide()
            return
        self._fade.setDuration(FAST + 40)
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._closing:
            self.hide()

    def _on_action(self) -> None:
        callback = self._callback
        self.dismiss()
        if callback is not None:
            callback()

    def _target_pos(self) -> QPoint:
        host = self.parentWidget()
        return QPoint((host.width() - self.width()) // 2, host.height() - self.height() - 46)

    def enterEvent(self, e):  # noqa: N802
        self._timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        if self.isVisible() and not self._closing:
            self._timer.start(1800)
        super().leaveEvent(e)

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self.parentWidget() and e.type() == QEvent.Type.Resize and self.isVisible():
            self._move.stop()
            self.move(self._target_pos())
        return False


class _AppPolish(QObject):
    """App-wide finishing: rounded translucent menus and soft dialog entrances."""

    def eventFilter(self, obj, e):  # noqa: N802
        t = e.type()
        if t == QEvent.Type.Polish:
            if isinstance(obj, QMenu) and not obj.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
                # Must happen before the native window exists, or corners stay square.
                obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        elif t == QEvent.Type.Show and isinstance(obj, QDialog) and obj.isWindow():
            fade_window_in(obj, FAST + 40)
        return False


def install(app: QApplication) -> None:
    if getattr(app, "_fx_polish", None) is None:
        app._fx_polish = _AppPolish(app)
        app.installEventFilter(app._fx_polish)
