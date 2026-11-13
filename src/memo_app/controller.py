"""编排层：全局热键、系统托盘、快捷记录弹窗、桌面挂件。"""
from __future__ import annotations

import logging
import sqlite3

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import db
from . import nlp_time
from .capture import QuickCapture
from .icons import make_icon
from .widget import TodoWidget

# 默认全局呼出快捷键（QSettings 可改）
DEFAULT_HOTKEY = "ctrl+o"

logger = logging.getLogger("memo_app.controller")

# ---- 快捷键规格解析/匹配/格式化 ----

# 修饰词 -> 规范名
_MOD_ALIAS = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "cmd": "cmd", "win": "cmd", "meta": "cmd", "super": "cmd",
}
_MOD_DISPLAY = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "cmd": "Win"}
_MOD_ORDER = ("ctrl", "shift", "alt", "cmd")


def _mod_family(kb, name: str) -> tuple:
    return {
        "ctrl": (kb.Key.ctrl, kb.Key.ctrl_l, kb.Key.ctrl_r),
        "shift": (kb.Key.shift, kb.Key.shift_l, kb.Key.shift_r),
        "alt": (kb.Key.alt, kb.Key.alt_l, kb.Key.alt_r),
        "cmd": (kb.Key.cmd, kb.Key.cmd_l, kb.Key.cmd_r),
    }[name]


# 特殊键名 -> pynput Key 属性名
_NAMED_KEYS = {
    "space": "space", "tab": "tab", "esc": "esc", "escape": "esc",
    "enter": "enter", "return": "enter",
    "backspace": "backspace", "delete": "delete", "del": "delete",
    "insert": "insert", "ins": "insert",
    "home": "home", "end": "end",
    "pageup": "page_up", "prior": "page_up",
    "pagedown": "page_down", "next": "page_down",
    "up": "up", "down": "down", "left": "left", "right": "right",
}
_NAMED_DISPLAY = {
    "space": "Space", "tab": "Tab", "esc": "Esc", "enter": "Enter",
    "backspace": "Backspace", "delete": "Delete", "insert": "Insert",
    "home": "Home", "end": "End", "page_up": "PageUp", "page_down": "PageDown",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
}
# 标点 -> 虚拟键码
_PUNCT_VK = {
    ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0, "-": 0xBD, "=": 0xBB,
}
_PUNCT_INV = {v: k for k, v in _PUNCT_VK.items()}


def _resolve_key(tok: str):
    """把快捷键字符串的最后一段解析为 ('vk', code) 或 ('name', attr)。"""
    if len(tok) == 1:
        c = tok
        if c.isascii() and c.isalpha():
            return ("vk", ord(c.upper()))
        if c.isascii() and c.isdigit():
            return ("vk", ord(c))
        if c == " ":
            return ("name", "space")
        if c in _PUNCT_VK:
            return ("vk", _PUNCT_VK[c])
        return None
    if tok in _NAMED_KEYS:
        return ("name", _NAMED_KEYS[tok])
    if tok.startswith("f") and tok[1:].isdigit() and 1 <= int(tok[1:]) <= 12:
        return ("name", tok)
    return None


def parse_hotkey(spec: str):
    """'ctrl+shift+k' -> (mods frozenset, kind, value)；非法返回 None。"""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    key = _resolve_key(parts[-1])
    if key is None:
        return None
    mods = set()
    for m in parts[:-1]:
        canon = _MOD_ALIAS.get(m)
        if canon is None:
            return None
        mods.add(canon)
    if not mods:
        return None
    return (frozenset(mods), key[0], key[1])


def format_spec(spec) -> str:
    """把 parse_hotkey 的结果格式化为 'Ctrl+Shift+K' 展示串。"""
    mods, kind, value = spec
    key_desc = ""
    if kind == "vk":
        if 0x41 <= value <= 0x5A or 0x30 <= value <= 0x39:
            key_desc = chr(value)
        elif value in _PUNCT_INV:
            key_desc = _PUNCT_INV[value].upper()
        elif value == 0x20:
            key_desc = "Space"
        else:
            key_desc = "?"
    else:
        key_desc = _NAMED_DISPLAY.get(value, value.upper())
    return "+".join([_MOD_DISPLAY[m] for m in _MOD_ORDER if m in mods] + [key_desc])


# ---- 全局热键：GetAsyncKeyState 实时状态查询 ----
# 不依赖 press/release 事件配对，免疫丢事件导致的粘滞键误触发。

_VK_SHIFT, _VK_CTRL, _VK_ALT = 0x10, 0x11, 0x12
_VK_LSHIFT, _VK_RSHIFT = 0xA0, 0xA1
_VK_LCTRL, _VK_RCTRL = 0xA2, 0xA3
_VK_LALT, _VK_RALT = 0xA4, 0xA5
_VK_LWIN, _VK_RWIN = 0x5B, 0x5C

# 修饰名 -> 判定按下的虚拟键码集合（通用码 + 左右变体都查）
_MOD_VKS = {
    "ctrl": (_VK_CTRL, _VK_LCTRL, _VK_RCTRL),
    "shift": (_VK_SHIFT, _VK_LSHIFT, _VK_RSHIFT),
    "alt": (_VK_ALT, _VK_LALT, _VK_RALT),
    "cmd": (_VK_LWIN, _VK_RWIN),
}

# pynput Key 属性名 -> 虚拟键码（named 键的"仍按住"判断用）
_ATTR_VK = {
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "enter": 0x0D,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
# 虚拟键码 -> 展示名（录制用）
_VK_NAMED = {vk: disp for attr, disp in _NAMED_DISPLAY.items()
             for vk in [_ATTR_VK.get(attr)] if vk is not None}
_VK_NAMED.update({0x6F + i: f"F{i}" for i in range(1, 13)})

_VK_DOWN = 0x8000


def _get_user32():
    try:
        import ctypes
        return ctypes.windll.user32
    except Exception:
        return None


def _vk_down(user32, vk: int) -> bool:
    try:
        return bool(user32.GetAsyncKeyState(vk) & _VK_DOWN)
    except Exception:
        return False


def _vk_desc(vk: int) -> str:
    """虚拟键码 -> 快捷键展示名；无法识别返回 ''。"""
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if vk in _PUNCT_INV:
        return _PUNCT_INV[vk].upper()
    if vk == 0x20:
        return "Space"
    return _VK_NAMED.get(vk, "")


class Controller(QObject):
    hotkey_pressed = Signal()

    def __init__(self, win: "MainWindow") -> None:  # noqa: F821
        super().__init__()
        self.win = win
        self.win.controller = self
        self._listener = None
        self._widget_enabled = True
        self._quitting = False

        # 快捷键规格（QSettings 持久化，默认 ctrl+o）——须在托盘之前初始化
        from PySide6.QtCore import QSettings
        spec_str = str(QSettings().value("hotkey", DEFAULT_HOTKEY))
        self._hotkey_spec = parse_hotkey(spec_str) or parse_hotkey(DEFAULT_HOTKEY)

        # 快捷记录弹窗
        self.capture = QuickCapture()
        self.capture.submitted.connect(self._on_capture_submit)
        self.hotkey_pressed.connect(self.capture.toggle)

        # 桌面待办挂件（分组默认折叠，隐私由挂件自身控制）
        self.widget = TodoWidget()
        self.widget._memo_provider = self.win.visible_memos
        self.widget.open_main.connect(self.show_main)
        self.widget.dismissed.connect(lambda: self._on_widget_toggled(False))
        self.widget.done_toggled.connect(self.win.toggle_done)
        self.win.memos_changed.connect(self.widget.reload)
        self.widget.reload()

        # 截止提醒：每 30s 扫一次已到期未提醒的备忘录
        self._remind_timer = QTimer(self)
        self._remind_timer.setInterval(30_000)
        self._remind_timer.timeout.connect(self._check_reminders)
        self._remind_timer.start()
        QTimer.singleShot(3000, self._check_reminders)

        # 系统托盘
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()
            win.tray_available = True
        self.win.installEventFilter(self)
        QApplication.instance().aboutToQuit.connect(self._prepare_quit)
        QTimer.singleShot(0, self, self._sync_widget_visibility)

        # 全局热键：pynput Listener（独立线程）只负责"按下事件"，
        # 修饰键与触发键状态一律用 GetAsyncKeyState 实时查询。
        self._combo_down = False  # 本次组合键已触发，防按键重复误触
        self._kb = None
        try:
            from pynput import keyboard as pynput_keyboard

            self._kb = pynput_keyboard

            def _on_press(key):
                if self._combo_down:
                    # release 丢失的自愈：触发键已不在按下状态则复位
                    if not self._trigger_still_down():
                        self._combo_down = False
                    else:
                        return
                if self._matches_hotkey(key):
                    self._combo_down = True
                    self.hotkey_pressed.emit()

            def _on_release(key):
                # 组合键中任一成员松开即复位（下次完整按下才再触发）
                if self._combo_down and self._is_combo_key(key):
                    self._combo_down = False

            self._listener = pynput_keyboard.Listener(
                on_press=_on_press, on_release=_on_release
            )
            self._listener.daemon = True
            self._listener.start()
        except Exception as e:
            self._listener = None
            logger.warning("全局热键监听启动失败，快捷键将不可用: %s", e)

    def _mods_held(self, user32, mods) -> bool:
        for m in mods:
            if not any(_vk_down(user32, vk) for vk in _MOD_VKS[m]):
                return False
        return True

    def _key_matches_spec(self, key) -> bool:
        """当前按下事件的键是否等于快捷键的触发键。"""
        if self._hotkey_spec is None or self._kb is None:
            return False
        _mods, kind, value = self._hotkey_spec
        if kind == "vk":
            return getattr(key, "vk", None) == value
        return key == getattr(self._kb.Key, value)

    def _trigger_still_down(self) -> bool:
        """触发键此刻是否仍按住（GetAsyncKeyState 实时查询）。"""
        if self._hotkey_spec is None:
            return False
        user32 = _get_user32()
        if user32 is None:
            return False
        _mods, kind, value = self._hotkey_spec
        if kind == "vk":
            return _vk_down(user32, value)
        vk = _ATTR_VK.get(value)
        return _vk_down(user32, vk) if vk is not None else False

    def _matches_hotkey(self, key) -> bool:
        """触发键匹配 且 所需修饰键此刻确实按住（实时状态）。"""
        if self._hotkey_spec is None:
            return False
        user32 = _get_user32()
        if user32 is None:
            return False
        mods = self._hotkey_spec[0]
        return self._key_matches_spec(key) and self._mods_held(user32, mods)

    def _is_combo_key(self, key) -> bool:
        """释放的键是否属于当前快捷键组合（触发键或所需修饰键）。"""
        if self._hotkey_spec is None or self._kb is None:
            return False
        if self._key_matches_spec(key):
            return True
        return any(key in _mod_family(self._kb, m) for m in self._hotkey_spec[0])

    # ---------- 快捷键设置 ----------

    def hotkey_display(self) -> str:
        return format_spec(self._hotkey_spec) if self._hotkey_spec else "?"

    def describe_pressed(self) -> str:
        """把当前按住的组合键格式化为 'Ctrl+Shift+K'；无有效组合返回 ''。

        直接扫描虚拟键实时状态，不依赖 press/release 事件配对。
        """
        user32 = _get_user32()
        if user32 is None:
            return ""
        mods = {
            name for name, vks in _MOD_VKS.items()
            if any(_vk_down(user32, vk) for vk in vks)
        }
        key_desc = ""
        for vk in range(0x08, 0xFE):
            if _vk_down(user32, vk):
                d = _vk_desc(vk)
                if d:
                    key_desc = d
                    break
        if not mods or not key_desc:
            return ""
        return "+".join([_MOD_DISPLAY[m] for m in _MOD_ORDER if m in mods] + [key_desc])

    def set_hotkey(self, spec_str: str) -> bool:
        """校验并保存新快捷键，立即生效。返回是否成功。"""
        from PySide6.QtCore import QSettings
        spec = parse_hotkey(spec_str)
        if spec is None:
            return False
        self._hotkey_spec = spec
        self._combo_down = False
        QSettings().setValue("hotkey", spec_str.strip().lower())
        if self.tray is not None and hasattr(self, "a_capture"):
            self.a_capture.setText(f"快捷记录   {self.hotkey_display()}")
        return True

    def open_settings(self) -> None:
        self.show_main()
        from .settings_dialog import SettingsDialog
        SettingsDialog(self, self.win).exec()

    # ---------- 托盘 ----------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(22), self)
        self.tray.setToolTip("memo 备忘录")

        menu = QMenu()
        a_open = menu.addAction("打开主界面")
        self.a_capture = menu.addAction(f"快捷记录   {self.hotkey_display()}")
        a_settings = menu.addAction("设置…")
        self.a_widget = menu.addAction("自动显示桌面挂件")
        self.a_widget.setCheckable(True)
        self.a_widget.setChecked(self._widget_enabled)
        menu.addSeparator()
        a_quit = menu.addAction("退出")

        a_open.triggered.connect(self.show_main)
        self.a_capture.triggered.connect(self.capture.toggle)
        a_settings.triggered.connect(self.open_settings)
        self.a_widget.toggled.connect(self._on_widget_toggled)
        a_quit.triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_widget_toggled(self, on: bool) -> None:
        self._widget_enabled = on
        if hasattr(self, "a_widget"):
            with QSignalBlocker(self.a_widget):
                self.a_widget.setChecked(on)
        self._sync_widget_visibility()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.win and event.type() in (
            QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.WindowStateChange,
        ):
            # Qt updates visibility/window state during event delivery.
            QTimer.singleShot(0, self, self._sync_widget_visibility)
        return super().eventFilter(obj, event)

    def _sync_widget_visibility(self) -> None:
        # Automatic hiding must not switch off the user's tray preference.
        main_visible = self.win.isVisible()
        background = (main_visible and self.win.isMinimized()) or (
            not main_visible and self.win.tray_available
        )
        self.widget.setVisible(self._widget_enabled and not self._quitting and background)

    def _prepare_quit(self) -> None:
        self._quitting = True
        self.widget.hide()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_main()

    # ---------- 动作 ----------

    def _on_capture_submit(self, text: str, cat_name: str = "") -> None:
        if text.strip():
            # 分类名与记录同事务创建/归入（同步原子性）
            self.win.add_memo_text(text, cat_name=cat_name or None)
        elif cat_name:
            # 仅创建分类：备忘录为空时 add_memo_text 不会触发刷新
            try:
                db.find_or_create_category(cat_name)
            except (ValueError, sqlite3.Error):
                pass
            self.win.reload()

    def _check_reminders(self) -> None:
        """扫描到期备忘录，通过系统托盘气泡提醒。"""
        try:
            due = db.memos_due()
        except Exception:
            return
        for m in due:
            if self.tray is None:
                # 无托盘时无法弹气泡，跳过标记，等托盘可用再提醒
                continue
            db.mark_notified(m.id)
            self.tray.showMessage(
                "memo 提醒",
                f"{m.content[:40]}\n{nlp_time.format_due(m.due_ts)}",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
        self._refresh_badges()  # 顺带刷新逾期标红（含提前提醒已触发的条目）

    def _refresh_badges(self) -> None:
        """轻量刷新主列表徽标（逾期变红），不重建列表。"""
        try:
            lst = self.win.list
        except Exception:
            return
        for i in range(lst.count()):
            w = lst.itemWidget(lst.item(i))
            if w is not None and hasattr(w, "_badges_html"):
                w.badges_label.setText(w._badges_html())
                w.badges_label.setVisible(bool(w.badges_label.text()))
                QTimer.singleShot(0, w, w._update_height)

    def show_main(self) -> None:
        self.widget.hide()
        self.win.show()
        if self.win.isMinimized():
            self.win.setWindowState(Qt.WindowState.WindowNoState)
        self.win.raise_()
        self.win.activateWindow()

    def quit(self) -> None:
        self._prepare_quit()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        QApplication.quit()
