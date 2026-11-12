"""编排层：全局热键、系统托盘、快捷记录弹窗、桌面挂件。"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import db
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
        if c.isalpha():
            return ("vk", ord(c.upper()))
        if c.isdigit():
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


class Controller(QObject):
    hotkey_pressed = Signal()

    def __init__(self, win: "MainWindow") -> None:  # noqa: F821
        super().__init__()
        self.win = win
        self.win.controller = self
        self._listener = None

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
        self.widget.done_toggled.connect(self.win.toggle_done)
        self.win.memos_changed.connect(self.widget.reload)
        self.widget.reload()

        # 系统托盘
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()
            self.widget.show()
            win.tray_available = True

        # 全局热键：pynput Listener（独立线程）+ 手动组合键匹配。
        # 实测 GlobalHotKeys 在本机不触发回调，改为自己跟踪按键状态。
        self._pressed_keys: set = set()
        self._kb = None
        try:
            from pynput import keyboard as pynput_keyboard

            self._kb = pynput_keyboard

            def _on_press(key):
                was = self._hotkey_active()
                self._pressed_keys.add(key)
                if self._hotkey_active() and not was:
                    self.hotkey_pressed.emit()

            def _on_release(key):
                self._pressed_keys.discard(key)

            self._listener = pynput_keyboard.Listener(
                on_press=_on_press, on_release=_on_release
            )
            self._listener.daemon = True
            self._listener.start()
        except Exception as e:
            self._listener = None
            logger.warning("全局热键监听启动失败，Ctrl+O 将不可用: %s", e)

    def _hotkey_active(self) -> bool:
        """当前配置的组合键是否处于按下状态（在监听线程中调用）。"""
        kb = self._kb
        if kb is None or self._hotkey_spec is None:
            return False
        mods, kind, value = self._hotkey_spec
        for m in mods:
            if not any(k in _mod_family(kb, m) for k in self._pressed_keys):
                return False
        if kind == "vk":
            # 'o' 键按住 Ctrl 时 char 会变成控制字符，须按虚拟键码判断
            return any(getattr(k, "vk", None) == value for k in self._pressed_keys)
        return any(k == getattr(kb.Key, value) for k in self._pressed_keys)

    # ---------- 快捷键设置 ----------

    def hotkey_display(self) -> str:
        return format_spec(self._hotkey_spec) if self._hotkey_spec else "?"

    def describe_pressed(self) -> str:
        """把当前按下的组合键格式化为 'Ctrl+Shift+K'；无有效组合返回 ''。"""
        kb = self._kb
        if kb is None:
            return ""
        rev_named = {getattr(kb.Key, a): d for a, d in _NAMED_DISPLAY.items()}
        rev_named.update({getattr(kb.Key, f"f{i}"): f"F{i}" for i in range(1, 13)})
        mods, key_desc = set(), ""
        for k in self._pressed_keys:
            hit = False
            for canon in _MOD_ORDER:
                if k in _mod_family(kb, canon):
                    mods.add(canon)
                    hit = True
                    break
            if hit or key_desc:
                continue
            vk = getattr(k, "vk", None)
            if vk and 0x41 <= vk <= 0x5A:
                key_desc = chr(vk)
            elif vk and 0x30 <= vk <= 0x39:
                key_desc = chr(vk)
            elif vk in _PUNCT_INV:
                key_desc = _PUNCT_INV[vk].upper()
            elif vk == 0x20:
                key_desc = "Space"
            elif k in rev_named:
                key_desc = rev_named[k]
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
        self.a_widget = menu.addAction("桌面挂件")
        self.a_widget.setCheckable(True)
        self.a_widget.setChecked(True)
        menu.addSeparator()
        a_quit = menu.addAction("退出")

        a_open.triggered.connect(self.show_main)
        self.a_capture.triggered.connect(self.capture.toggle)
        a_settings.triggered.connect(self.open_settings)
        self.a_widget.toggled.connect(self._on_widget_toggled)
        # 挂件被 × 隐藏时同步托盘勾选状态
        self.widget.visibility_changed.connect(self.a_widget.setChecked)
        a_quit.triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_widget_toggled(self, on: bool) -> None:
        self.widget.setVisible(on)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_main()

    # ---------- 动作 ----------

    def _on_capture_submit(self, text: str, cat_name: str = "") -> None:
        cat_ids = None
        if cat_name:
            cat_ids = [db.find_or_create_category(cat_name)]
        if text.strip():
            self.win.add_memo_text(text, cat_ids)
        elif cat_name:
            # 仅创建分类：备忘录为空时 add_memo_text 不会触发刷新
            self.win.reload()

    def show_main(self) -> None:
        self.win.show()
        if self.win.isMinimized():
            self.win.setWindowState(Qt.WindowState.WindowNoState)
        self.win.raise_()
        self.win.activateWindow()

    def quit(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        QApplication.quit()
