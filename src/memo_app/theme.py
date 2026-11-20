"""Skins for 轻笺 · Lumo: tokenized palettes + one QSS template.

Every colour in the UI comes from the active palette, either through the
generated stylesheet or through ``c(token)`` for custom-painted widgets.
Switching skins re-applies the stylesheet and emits ``notifier.changed`` so
painted widgets and icons can refresh.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from string import Template

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QColor

APP_NAME = "轻笺"
APP_NAME_EN = "Lumo"
APP_VERSION = "0.4.0"

_ASSETS = Path(__file__).parent / "assets"
_CHECK = (_ASSETS / "check.png").as_posix()

PINE = {
    "label": "松绿", "dark": False,
    "bg": "#FAFBF8", "side": "#F1F3ED", "titlebar": "#EEF1EA",
    "content_a": "#FCFCF9", "content_b": "#F7F8F4",
    "surface": "#FFFFFF", "surface_alt": "#EDF0E8", "surface_muted": "#F2F4EE", "selected": "#F4F8F0",
    "border": "#E1E6DC", "border_hover": "#C3D1BE", "line": "#E3E8DF",
    "text": "#283D35", "text_2": "#42594B", "muted": "#66736B", "faint": "#85918A",
    "accent": "#2D6550", "accent_hover": "#357459", "accent_press": "#234F3F",
    "accent_border": "#285A47", "on_accent": "#FFFFFF",
    "accent_soft": "#DEE8DB", "hover": "#E5EAE0", "press": "#D5DFD1",
    "accent_text": "#284F3D", "focus": "#648D72",
    "danger": "#B4533E", "danger_soft": "#F9E7E1", "danger_text": "#9B523D",
    "warn": "#8D633E", "warn_soft": "#F7EDE0", "prio": "#B87959", "overdue": "#AC503B",
    "tag": "#4F765F", "flash": "#E4F0DE", "flash_border": "#7FA584", "shadow": "#283D35",
    "trash_bg": "#F5EDE2", "trash_border": "#E9DCC7", "trash_text": "#7D674B", "trash_hover": "#EDE0CE",
    "toast_bg": "#284C3B", "toast_border": "#1F3E30", "toast_text": "#F4F7F1", "toast_accent": "#CFE3C6",
    "scroll": "#D3DCCD", "scroll_hover": "#A6B69D",
    "widget_more_text": "#D3DCCB", "widget_more_hover_text": "#E2EBD9",
    "widget_more_hover": "rgba(209, 224, 196, 35)", "widget_more_press": "rgba(209, 224, 196, 60)",
}

INK = {
    "label": "墨夜", "dark": True,
    "bg": "#16181B", "side": "#1B1E22", "titlebar": "#191C1F",
    "content_a": "#1F2226", "content_b": "#1B1E21",
    "surface": "#25292E", "surface_alt": "#2A2F35", "surface_muted": "#212428", "selected": "#243530",
    "border": "#32373D", "border_hover": "#4A535C", "line": "#2C3136",
    "text": "#E4E8E5", "text_2": "#C9D0CB", "muted": "#9AA49E", "faint": "#7D8781",
    "accent": "#5DBB98", "accent_hover": "#6CC8A5", "accent_press": "#4AA483",
    "accent_border": "#4FA886", "on_accent": "#0D1F17",
    "accent_soft": "#25392F", "hover": "#262B30", "press": "#2F363C",
    "accent_text": "#8ED8BA", "focus": "#5DBB98",
    "danger": "#E2806E", "danger_soft": "#3A2624", "danger_text": "#F09A89",
    "warn": "#DDB57A", "warn_soft": "#3A3122", "prio": "#D98E67", "overdue": "#EE8370",
    "tag": "#84C9AB", "flash": "#22392F", "flash_border": "#5DBB98", "shadow": "#000000",
    "trash_bg": "#2B2620", "trash_border": "#3D342A", "trash_text": "#D8C1A0", "trash_hover": "#3A3128",
    "toast_bg": "#E4ECE7", "toast_border": "#C9D6CE", "toast_text": "#17211C", "toast_accent": "#1F6F52",
    "scroll": "#3A4148", "scroll_hover": "#56616B",
    "widget_more_text": "#D3DCCB", "widget_more_hover_text": "#E2EBD9",
    "widget_more_hover": "rgba(209, 224, 196, 35)", "widget_more_press": "rgba(209, 224, 196, 60)",
}

LAVENDER = {
    "label": "薰衣草", "dark": False,
    "bg": "#FAF9FD", "side": "#F3F1F8", "titlebar": "#F0EDF6",
    "content_a": "#FDFCFF", "content_b": "#F8F6FC",
    "surface": "#FFFFFF", "surface_alt": "#EFECF5", "surface_muted": "#F4F2F8", "selected": "#F6F3FD",
    "border": "#E3DEEC", "border_hover": "#CBC2DD", "line": "#E6E2EE",
    "text": "#2D2940", "text_2": "#46405E", "muted": "#6A6480", "faint": "#8D87A2",
    "accent": "#6B55C8", "accent_hover": "#7965D3", "accent_press": "#5643AE",
    "accent_border": "#5A46B4", "on_accent": "#FFFFFF",
    "accent_soft": "#E8E2F8", "hover": "#ECE8F4", "press": "#DFD8EE",
    "accent_text": "#4A3A98", "focus": "#8A77D6",
    "danger": "#B5485A", "danger_soft": "#F8E6EA", "danger_text": "#9D3E50",
    "warn": "#8A6A2E", "warn_soft": "#F6EEDD", "prio": "#C0718A", "overdue": "#B0434F",
    "tag": "#6650B8", "flash": "#EAE4FA", "flash_border": "#9C8BE0", "shadow": "#2D2940",
    "trash_bg": "#F7EEF2", "trash_border": "#EBD9E2", "trash_text": "#7E5468", "trash_hover": "#EFDFE7",
    "toast_bg": "#3A2F66", "toast_border": "#2E2552", "toast_text": "#F6F4FF", "toast_accent": "#D9CFFF",
    "scroll": "#D9D3E6", "scroll_hover": "#B3A8CC",
    "widget_more_text": "#D3DCCB", "widget_more_hover_text": "#E2EBD9",
    "widget_more_hover": "rgba(209, 224, 196, 35)", "widget_more_press": "rgba(209, 224, 196, 60)",
}

MICA = {
    "label": "云母蓝", "dark": False,
    "bg": "#F3F5F9", "side": "#EDF1F6", "titlebar": "#EDF1F6",
    "content_a": "#F9FAFC", "content_b": "#F6F8FB",
    "surface": "#FFFFFF", "surface_alt": "#EBEFF5", "surface_muted": "#F2F4F8", "selected": "#E6EEFD",
    "border": "#E0E5ED", "border_hover": "#C4CEDC", "line": "#E3E7EE",
    "text": "#1B2430", "text_2": "#3A4656", "muted": "#647184", "faint": "#8995A7",
    "accent": "#2F6FEB", "accent_hover": "#3E7CF0", "accent_press": "#2459C4",
    "accent_border": "#2A63D6", "on_accent": "#FFFFFF",
    "accent_soft": "#E3ECFD", "hover": "#E7EBF2", "press": "#DBE2EC",
    "accent_text": "#1A4FB8", "focus": "#5B8DEF",
    "danger": "#C8452F", "danger_soft": "#FBE8E4", "danger_text": "#B23B27",
    "warn": "#B9590B", "warn_soft": "#FDF0E3", "prio": "#E0661F", "overdue": "#D9480F",
    "tag": "#4467A8", "flash": "#E4EDFD", "flash_border": "#7FA3EE", "shadow": "#1B2A44",
    "trash_bg": "#F7EFE6", "trash_border": "#EADBC8", "trash_text": "#7A5E3E", "trash_hover": "#EFE2D1",
    "toast_bg": "#1F2A3A", "toast_border": "#152031", "toast_text": "#F2F5FA", "toast_accent": "#9DBBFF",
    "scroll": "#CDD5E1", "scroll_hover": "#A3B0C3",
    "widget_more_text": "#D3DCCB", "widget_more_hover_text": "#E2EBD9",
    "widget_more_hover": "rgba(209, 224, 196, 35)", "widget_more_press": "rgba(209, 224, 196, 60)",
}

PAPER = {
    "label": "宣纸", "dark": False,
    "bg": "#F5F0E6", "side": "#F0EADE", "titlebar": "#F0EADE",
    "content_a": "#F7F2E9", "content_b": "#F4EEE3",
    "surface": "#FBF8F1", "surface_alt": "#EBE3D4", "surface_muted": "#F1EBDF", "selected": "#F2E8D9",
    "border": "#E1D7C5", "border_hover": "#CABAA1", "line": "#DDD2BF",
    "text": "#2A2521", "text_2": "#4E453D", "muted": "#83786B", "faint": "#A29788",
    "accent": "#B4412E", "accent_hover": "#C04E3A", "accent_press": "#963424",
    "accent_border": "#9E3828", "on_accent": "#FFF8F0",
    "accent_soft": "#F1DDD3", "hover": "#ECE4D6", "press": "#E3D8C5",
    "accent_text": "#9A3626", "focus": "#C9735F",
    "danger": "#B4412E", "danger_soft": "#F4E0D8", "danger_text": "#9A3626",
    "warn": "#9A6A2E", "warn_soft": "#F3E8D6", "prio": "#B4412E", "overdue": "#B4412E",
    "tag": "#6B5E50", "flash": "#F2E6D3", "flash_border": "#C9A27E", "shadow": "#3A2E22",
    "trash_bg": "#EFE6D6", "trash_border": "#E0D2BA", "trash_text": "#6E5A40", "trash_hover": "#E6DAC6",
    "toast_bg": "#2A2521", "toast_border": "#1E1A17", "toast_text": "#F5F0E6", "toast_accent": "#F0B9A8",
    "scroll": "#D8CDBB", "scroll_hover": "#B9AA92",
    "widget_more_text": "#D3DCCB", "widget_more_hover_text": "#E2EBD9",
    "widget_more_hover": "rgba(209, 224, 196, 35)", "widget_more_press": "rgba(209, 224, 196, 60)",
}

# 分类标记色（按分类 id 轮转）：同一分类在侧栏/列表/详情里颜色一致
for _pal, _cats in (
    (PINE, ("#2F6B45", "#B5562A", "#5B47B5", "#2F6FA8", "#A8781F", "#8A4A78")),
    (INK, ("#7FA8FF", "#F2A65A", "#B49CFF", "#5FD3B5", "#F28BB0", "#B8CC6A")),
    (LAVENDER, ("#6B55C8", "#C0718A", "#2F8F86", "#B07A2A", "#4A6FC8", "#8A5A9E")),
    (MICA, ("#2F6FEB", "#E07B12", "#8B5CF6", "#16A085", "#D6457A", "#6B7F2A")),
    (PAPER, ("#3F6B4F", "#B4412E", "#5B4A9E", "#9A6A2E", "#2F5F86", "#7A4A6B")),
):
    _pal["cat_colors"] = _cats

PALETTES = {"mica": MICA, "paper": PAPER, "pine": PINE, "ink": INK, "lavender": LAVENDER}
DEFAULT = "mica"

# ---------- 风格：布局 / 字体 / 列表画法，与配色正交（可任意组合） ----------
# font_* 是 QSS font-family 回退链：Qt 按字符逐个回退，西文与中文各取最合适的字体。
_SANS = '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif'
_SEGOE = '"Segoe UI Variable Text", "Segoe UI", ' + _SANS
_SERIF_DISPLAY = ('"Noto Serif SC", "Source Han Serif SC", "Source Han Serif CN", '
                  '"STSong", "SimSun", serif')
# 正文没有好宋体时退回雅黑：SimSun 在正文字号用点阵字形，观感粗糙
_SERIF_BODY = ('"Noto Serif SC", "Source Han Serif SC", "Source Han Serif CN", '
               '"STSong", "Microsoft YaHei UI", serif')
_KAI = '"STKaiti", "KaiTi", "Kaiti SC", ' + _SERIF_BODY

STYLES = {
    "mica": {
        "label": "云母", "palette": "mica",
        "desc": "三栏 · 详情常驻 · 按日期分组",
        "font_ui": _SEGOE,
        "font_title": '"Segoe UI Variable Display", "Segoe UI", ' + _SANS,
        "font_body": _SEGOE,
        "font_hint": _SEGOE,
        "radius": "8px", "radius_sm": "6px",
    },
    "paper": {
        "label": "纸笺", "palette": "paper",
        "desc": "宋体留白 · 细线分隔 · 详情按需展开",
        "font_ui": _SANS,
        "font_title": _SERIF_DISPLAY,
        "font_body": _SERIF_BODY,
        "font_hint": _KAI,
        "radius": "4px", "radius_sm": "3px",
    },
}
DEFAULT_STYLE = "mica"


class _Notifier(QObject):
    changed = Signal(str)


notifier = _Notifier()
_current = DEFAULT
_style = DEFAULT_STYLE


def current_name() -> str:
    return _current


def style_name() -> str:
    return _style


def is_paper() -> bool:
    return _style == "paper"


def saved_style() -> str:
    name = str(QSettings().value("ui_style", DEFAULT_STYLE))
    return name if name in STYLES else DEFAULT_STYLE


def cat_color(cat_id: int) -> str:
    """分类标记色（按 id 轮转当前配色的分类色）。"""
    colors = palette()["cat_colors"]
    return colors[(max(1, cat_id) - 1) % len(colors)]


def palette(name: str | None = None) -> dict:
    return PALETTES.get(name or _current, PINE)


def c(token: str, name: str | None = None) -> str:
    """Hex colour of a token in the active (or given) palette."""
    return palette(name)[token]


def qc(token: str, alpha: int | None = None) -> QColor:
    color = QColor(c(token))
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def is_dark() -> bool:
    return bool(palette()["dark"])


def saved_name() -> str:
    value = QSettings().value("theme")
    if value is None:
        # 首次运行：配色跟随风格的推荐配色
        return STYLES[saved_style()]["palette"]
    name = str(value)
    return name if name in PALETTES else DEFAULT


def _chevron(color: str) -> str:
    """Comboboxes need an image file; write one tinted copy per palette."""
    folder = Path(tempfile.gettempdir()) / "lumo-ui"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"chevron_{color.lstrip('#')}.svg"
    if not path.exists():
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="m6 9 6 6 6-6"/></svg>', encoding="ascii")
    return path.as_posix()


_TEMPLATE = Template("""
QWidget { color: $text; font-family: $font_ui; }
QWidget#central { background: $bg; }
QLabel { background: transparent; border: none; }
QWidget#titleBar { background: $titlebar; border-bottom: 1px solid $line; }
QLabel#tbTitle { font-size: 12px; color: $text_2; font-weight: 600; }
QToolButton#navBtn, QToolButton#winBtn, QToolButton#closeBtn {
    border: none; background: transparent; border-radius: 6px;
    color: $text_2; padding: 4px 10px; font-size: 12px;
}
QToolButton#navBtn:hover, QToolButton#winBtn:hover { background: $hover; color: $accent_text; }
QToolButton#navBtn:pressed, QToolButton#winBtn:pressed, QToolButton#navBtn:open { background: $press; }
QToolButton#navBtn::menu-indicator { image: none; }
QToolButton#closeBtn:hover, QToolButton#closeBtn:focus { background: #C94F3D; }
QToolButton#closeBtn:pressed { background: #A9402F; }
QLineEdit#titleSearch {
    background: $surface_alt; border: 1px solid transparent; border-radius: 8px;
    padding: 5px 8px; font-size: 12px; color: $text;
}
QLineEdit#titleSearch:hover { border-color: $border; }
QLineEdit#titleSearch:focus { background: $surface; border-color: $focus; }

QWidget#sidePanel { background: $side; border-right: 1px solid $line; }
QLabel#brandName { font-size: 18px; font-weight: 700; color: $text; }
QLabel#brandSub { font-size: 11px; color: $muted; }
QLabel#sectionLabel { font-size: 11px; color: $muted; font-weight: 600; }
QToolButton#sectionAdd {
    border: none; background: transparent; border-radius: 6px; padding: 2px;
}
QToolButton#sectionAdd:hover { background: $hover; }
QToolButton#sectionAdd:pressed { background: $press; }
QWidget#sideFooter { border-top: 1px solid $line; }
QToolButton#footerBtn {
    border: 1px solid transparent; background: transparent; border-radius: 8px;
    color: $text_2; padding: 6px 10px; font-size: 12px;
}
QToolButton#footerBtn:hover { background: $hover; color: $accent_text; }
QToolButton#footerBtn:pressed, QToolButton#footerBtn:open { background: $press; }
QToolButton#footerBtn:focus { border-color: $focus; }
QToolButton#footerBtn::menu-indicator { image: none; }

QWidget#catRow { border: 1px solid transparent; border-radius: 8px; }
QWidget#catRow:hover { background: $hover; }
QWidget#catRow[selected="true"] { background: $accent_soft; }
QWidget#catRow:focus { border: 1px solid $focus; }
QLabel#catName { font-size: 12px; color: $text_2; }
QWidget#catRow[selected="true"] QLabel#catName { color: $accent_text; font-weight: 600; }
QLabel#catCount {
    font-size: 11px; color: $muted; padding: 0 7px; border-radius: 9px; background: transparent;
}
QWidget#catRow[selected="true"] QLabel#catCount { background: $surface; color: $accent; }
QPushButton#catDel { border: none; background: transparent; padding: 0; border-radius: 4px; }
QPushButton#catDel:hover { background: $danger_soft; }

QWidget#listPanel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 $content_a, stop:1 $content_b);
}
QLabel#pageTitle { color: $text; font-size: 24px; font-weight: 700; font-family: $font_title; }
QLabel#pageTitle[compact="true"] { font-size: 21px; }
QLabel#pageCount {
    color: $accent_text; background: $accent_soft; font-size: 12px; font-weight: 600;
    border-radius: 10px; padding: 1px 9px;
}
QLabel#pageSubtitle { color: $muted; font-size: 12px; }
QLabel#dateLabel {
    color: $text_2; font-size: 11px; background: $surface_alt; border-radius: 9px; padding: 3px 10px;
}
QLineEdit {
    border: 1px solid $border; border-radius: 8px; padding: 8px 10px;
    background: $surface; color: $text;
    selection-background-color: $accent_soft; selection-color: $text;
}
QLineEdit:hover { border-color: $border_hover; }
QLineEdit:focus { border-color: $focus; }
QLineEdit:disabled { color: $faint; background: $surface_muted; }
QLineEdit#mainInput { border: none; background: transparent; padding: 8px 0; font-size: 15px; }
QLabel#composerHint { color: $muted; font-size: 11px; }

QPushButton {
    border: 1px solid $border; border-radius: 8px; padding: 7px 16px;
    background: $surface; color: $text_2;
}
QPushButton:hover { background: $hover; border-color: $border_hover; color: $accent_text; }
QPushButton:pressed { background: $press; }
QPushButton:focus { border-color: $focus; }
QPushButton:disabled { color: $faint; background: $surface_muted; border-color: $line; }
QPushButton:default, QPushButton#saveBtn, QPushButton#primaryBtn, QPushButton#recordBtn:checked {
    background: $accent; color: $on_accent; border: 1px solid $accent_border; font-weight: 600;
}
QPushButton:default:hover, QPushButton#saveBtn:hover, QPushButton#primaryBtn:hover {
    background: $accent_hover; color: $on_accent;
}
QPushButton:default:pressed, QPushButton#saveBtn:pressed, QPushButton#primaryBtn:pressed {
    background: $accent_press;
}
QPushButton#saveBtn { padding: 0 16px; border-radius: 9px; }
QPushButton#saveBtn:disabled, QPushButton#primaryBtn:disabled {
    background: $surface_alt; color: $faint; border-color: $line; font-weight: 400;
}
QPushButton#ghostBtn { border-color: transparent; background: transparent; color: $accent_text; }
QPushButton#ghostBtn:hover { background: $hover; }
QPushButton#dangerBtn { color: $danger_text; border-color: $danger_soft; background: $surface; }
QPushButton#dangerBtn:hover { background: $danger_soft; border-color: $danger; color: $danger_text; }

QWidget#filterGroup { background: $surface_alt; border-radius: 9px; }
QPushButton#filterBtn {
    border: 1px solid transparent; padding: 6px 14px; border-radius: 7px;
    background: transparent; color: $muted; font-size: 11px;
}
QPushButton#filterBtn:hover { color: $accent_text; background: $hover; }
QPushButton#filterBtn:checked {
    background: $surface; color: $accent; border-color: $border; font-weight: 600;
}
QPushButton#filterBtn:focus { border-color: $focus; }
QLabel#listHeading { font-size: 11px; color: $muted; }

QLabel#memoContent { font-size: 14px; color: $text; font-family: $font_body; }
QLabel#memoContent[completed="true"] { color: $muted; }
QLabel#memoTime { font-size: 11px; color: $muted; }
QLabel#memoPin { background: transparent; padding: 0; }
QLabel#memoTags { font-size: 11px; color: $tag; }
QLabel#memoBadges { font-size: 11px; }
QPushButton#cardAction, QPushButton#deleteAction {
    border: 1px solid transparent; background: transparent; padding: 0; border-radius: 7px;
}
QPushButton#cardAction:hover { background: $hover; }
QPushButton#cardAction:pressed { background: $press; }
QPushButton#cardAction:focus { border: 1px solid $focus; }
QPushButton#deleteAction:hover { background: $danger_soft; }
QPushButton#deleteAction:focus { border: 1px solid $danger; }
QPushButton#doneBtn {
    border: 1.5px solid $border_hover; border-radius: 10px; padding: 0; background: $surface;
}
QPushButton#doneBtn:hover, QPushButton#doneBtn:focus { border-color: $accent; background: $accent_soft; }
QPushButton#doneBtn[done="true"] { background: $accent; border-color: $accent; }
QPushButton#doneBtn[prio="true"] { border-color: $prio; }
QLabel#groupHeader { font-size: 12px; color: $text_2; }
QPlainTextEdit#detailTitle {
    background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 0 2px;
    font-family: $font_body; font-size: 17px; font-weight: 600; color: $text;
    selection-background-color: $accent_soft; selection-color: $text;
}
QPlainTextEdit#detailTitle:hover { border-color: $line; }
QPlainTextEdit#detailTitle:focus { border-color: $focus; background: $surface; }
QLabel#detailCrumb, QLabel#detailCats { font-size: 12px; color: $text_2; }
QPushButton#chipGhost, QPushButton#chipDanger {
    border: 1px dashed $border_hover; border-radius: $radius_sm; padding: 5px 10px;
    background: transparent; color: $muted; font-size: 12px;
}
QPushButton#chipGhost:hover { border-style: solid; color: $accent_text; background: $hover; }
QPushButton#chipGhost:checked { border: 1px solid $warn; color: $warn; background: $warn_soft; }
QPushButton#chipDanger { border-style: solid; border-color: $line; }
QPushButton#chipDanger:hover { color: $danger_text; border-color: $danger; background: $danger_soft; }
QDateTimeEdit[overdue="true"] { color: $overdue; }
QLineEdit#inlineEdit { background: $selected; padding: 4px 8px; border-color: $focus; }

QListWidget, QScrollArea { background: transparent; border: none; outline: none; }
QListWidget::item { border: none; }
QListWidget::item:selected { background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QLabel#emptyIcon { background: $surface_alt; border: 1px solid $line; border-radius: 44px; }
QLabel#emptyTitle { color: $text_2; font-size: 19px; font-weight: 600; }
QLabel#emptyLabel { color: $muted; font-size: 12px; }
QLabel#statusLabel { color: $muted; font-size: 11px; }
QWidget#trashBar { background: $trash_bg; border: 1px solid $trash_border; border-radius: 9px; }
QLabel#trashTitle { color: $trash_text; font-size: 11px; }
QPushButton#trashBack { border: 1px solid transparent; background: transparent; padding: 4px 10px; color: $trash_text; }
QPushButton#trashBack:hover { background: $trash_hover; }
QPushButton#trashPurge { border-color: $trash_border; color: $danger_text; padding: 5px 12px; background: $surface; }
QPushButton#trashPurge:hover { background: $danger_soft; border-color: $danger; color: $danger_text; }
QPushButton#trashPurge:disabled { color: $faint; background: $surface_muted; border-color: $line; }

QWidget#toast { background: $toast_bg; border: 1px solid $toast_border; border-radius: 10px; }
QLabel#toastText { color: $toast_text; font-size: 12px; }
QPushButton#toastAction {
    border: none; background: transparent; color: $toast_accent;
    border-radius: 6px; padding: 4px 10px; font-weight: 700;
}
QPushButton#toastAction:hover { text-decoration: underline; }
QPushButton#toastAction:focus { border: 1px solid $toast_accent; }

QMenu { background: $surface; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 28px 8px 12px; border-radius: 6px; color: $text_2; background: transparent; }
QMenu::item:selected { background: $hover; color: $accent_text; }
QMenu::item:disabled { color: $faint; }
QMenu::separator { height: 1px; background: $line; margin: 5px 8px; }
QMenu::icon { padding-left: 8px; }

QDialog { background: $bg; }
QDialog QLabel { color: $text_2; }
QLabel#dialogTitle { font-size: 18px; font-weight: 700; color: $text; }
QLabel#dialogHint { font-size: 11px; color: $muted; }
QLabel#fieldLabel { font-size: 12px; color: $text_2; font-weight: 600; }
QComboBox, QDateTimeEdit {
    border: 1px solid $border; border-radius: 8px; padding: 7px 34px 7px 10px;
    background: $surface; color: $text_2;
}
QComboBox:hover, QDateTimeEdit:hover { border-color: $border_hover; }
QComboBox::drop-down, QDateTimeEdit::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none;
    border-left: 1px solid $line; border-top-right-radius: 8px; border-bottom-right-radius: 8px;
    background: $surface_alt;
}
QComboBox::down-arrow, QDateTimeEdit::down-arrow { image: url($chevron); width: 12px; height: 12px; }
QComboBox:focus, QDateTimeEdit:focus { border-color: $focus; }
QComboBox:disabled, QDateTimeEdit:disabled { color: $faint; background: $surface_muted; }
QComboBox QAbstractItemView {
    background: $surface; color: $text_2; border: 1px solid $border; outline: none;
    selection-background-color: $accent_soft; selection-color: $text;
}
QCalendarWidget QWidget { background: $surface; color: $text; }
QCalendarWidget QToolButton { color: $text; background: transparent; }
QCalendarWidget QAbstractItemView { selection-background-color: $accent; selection-color: $on_accent; }
QCheckBox { color: $text_2; spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid $border_hover; border-radius: 4px; background: $surface; }
QCheckBox::indicator:hover, QCheckBox:focus::indicator { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
QToolTip { background: $toast_bg; color: $toast_text; border: 1px solid $toast_border; padding: 6px 9px; }
QMessageBox { background: $bg; }

QWidget#settingsNav { background: $side; border-right: 1px solid $line; }
QLabel#settingsHeading { font-size: 18px; font-weight: 700; color: $text; }
QPushButton#settingsNavItem {
    text-align: left; border: 1px solid transparent; background: transparent;
    border-radius: 8px; padding: 9px 12px; color: $text_2; font-size: 12px;
}
QPushButton#settingsNavItem:hover { background: $hover; color: $accent_text; }
QPushButton#settingsNavItem:checked { background: $accent_soft; color: $accent_text; font-weight: 600; }
QPushButton#settingsNavItem:focus { border-color: $focus; }
QPushButton#backBtn {
    text-align: left; border: 1px solid transparent; background: transparent;
    border-radius: 8px; padding: 7px 10px; color: $muted; font-size: 12px;
}
QPushButton#backBtn:hover { background: $hover; color: $accent_text; }
QWidget#settingsBody { background: $content_a; }
QLabel#settingsTitle { font-size: 22px; font-weight: 700; color: $text; }
QLabel#settingsDesc { font-size: 12px; color: $muted; }
QLabel#groupTitle { font-size: 12px; font-weight: 700; color: $text_2; }
QWidget#settingsCard { background: $surface; border: 1px solid $border; border-radius: 12px; }
QWidget#settingsRow { background: transparent; }
QWidget#rowLine { background: $line; }
QLabel#rowTitle { font-size: 13px; color: $text; }
QLabel#rowDesc { font-size: 11px; color: $muted; }
QLabel#keycap {
    font-size: 11px; color: $text_2; background: $surface_alt; border: 1px solid $border;
    border-bottom-width: 2px; border-radius: 5px; padding: 2px 8px;
}
QLabel#notice {
    font-size: 11px; color: $warn; background: $warn_soft; border-radius: 8px; padding: 8px 12px;
}
QLabel#statusOk { font-size: 11px; color: $accent; }
QLabel#statusErr { font-size: 11px; color: $danger; }
QWidget#statusBanner { background: $surface_alt; border: 1px solid $border; border-radius: 12px; }
QLabel#aboutName { font-size: 26px; font-weight: 700; color: $text; }
""")


def build_qss(name: str | None = None, style_key: str | None = None) -> str:
    style_key = style_key if style_key in STYLES else _style
    pal = dict(palette(name))
    pal.update({k: v for k, v in STYLES[style_key].items() if k.startswith(("font_", "radius"))})
    pal["chevron"] = _chevron(pal["muted"])
    pal["check"] = _CHECK
    extra = _PAPER_QSS if style_key == "paper" else _MICA_QSS
    return _TEMPLATE.substitute(pal) + Template(extra).substitute(pal)


def apply(name: str | None = None, persist: bool = False, style_key: str | None = None) -> None:
    """Activate a skin (and optionally a style) app-wide.

    ``style_key=None`` keeps the current style. The stylesheet is rebuilt once;
    custom-painted widgets refresh via ``notifier``.
    """
    from PySide6.QtWidgets import QApplication

    global _current, _style
    name = name if name in PALETTES else saved_name()
    if style_key not in STYLES:
        style_key = _style
    changed = name != _current or style_key != _style
    _current, _style = name, style_key
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_qss(name, style_key))
    if persist:
        QSettings().setValue("theme", name)
        QSettings().setValue("ui_style", style_key)
    if changed:
        notifier.changed.emit(name)


def apply_style(style_key: str, persist: bool = True) -> None:
    """切换风格并换成该风格的推荐配色（之后仍可单独改配色）。"""
    if style_key in STYLES:
        apply(STYLES[style_key]["palette"], persist=persist, style_key=style_key)


# 风格专属补充规则：追加在通用模板之后，同优先级时后者生效
_MICA_QSS = """
QWidget#sidePanel { background: $side; border-right: none; }
QWidget#listPanel { background: $content_a; border-top: 1px solid $line; border-left: 1px solid $line;
    border-top-left-radius: 10px; }
QWidget#detailPanel { background: $surface; border-top: 1px solid $line; border-left: 1px solid $line; }
QWidget#catRow { border-radius: $radius_sm; }
QWidget#catRow[selected="true"] { background: $surface; }
QWidget#catRow[selected="true"] QLabel#catName { color: $text; }
QLabel#pageTitle { font-size: 22px; font-weight: 600; }
QLabel#groupHeader { font-size: 12px; font-weight: 600; color: $text_2; }
QLabel#groupHeader[warn="true"] { color: $overdue; }
"""

_PAPER_QSS = """
QWidget#titleBar { border-bottom: none; }
QLabel#tbTitle { font-family: $font_title; font-weight: 600; color: $text_2; }
QWidget#sidePanel { background: $side; border-right: 1px solid $line; }
QWidget#listPanel { background: transparent; }
QWidget#detailPanel { background: $surface; border-left: 1px solid $line; }
QLabel#brandName, QLabel#dialogTitle, QLabel#settingsTitle, QLabel#settingsHeading,
QLabel#emptyTitle, QPlainTextEdit#detailTitle { font-family: $font_title; }
QLabel#paperDay { font-family: $font_title; font-size: 60px; font-weight: 300; color: $text; }
QLabel#paperDate { font-family: $font_title; font-size: 13px; color: $text_2; }
QLabel#paperTerm { font-family: $font_hint; font-size: 12px; color: $accent; }
QWidget#catRow { border-radius: 0; }
QWidget#catRow:hover { background: transparent; }
QWidget#catRow:hover QLabel#catName { color: $text; }
QWidget#catRow[selected="true"] { background: transparent; }
QWidget#catRow[selected="true"] QLabel#catName { color: $text; font-weight: 700; }
QWidget#catRow[selected="true"] QLabel#catCount { background: transparent; color: $accent; }
QLabel#catName { font-family: $font_body; font-size: 14px; }
QLabel#catCount { font-family: $font_title; }
QLabel#sectionLabel { font-family: $font_title; font-weight: 400; }
QLabel#pageTitle { font-size: 28px; font-weight: 600; }
QLabel#pageSubtitle, QLabel#composerHint { font-family: $font_hint; font-size: 12px; }
QLineEdit#mainInput { font-family: $font_hint; font-size: 18px; }
QLabel#groupHeader { font-family: $font_title; font-size: 12px; color: $accent; }
QLabel#memoContent { font-size: 15px; }
QLabel#memoTime, QLabel#memoTags, QLabel#memoBadges { font-family: $font_title; }
QLabel#memoSeal { font-family: $font_hint; font-size: 12px; color: $accent;
    border: 1px solid $accent; border-radius: 3px; padding: 0; }
QPushButton#doneBtn { border: 1.3px solid $text_2; background: transparent; }
QPushButton#doneBtn[done="true"] { background: $text_2; border-color: $text_2; }
QWidget#filterGroup { background: transparent; border-radius: 0; }
QPushButton#filterBtn { font-family: $font_title; border-radius: 0; border-bottom: 2px solid transparent; }
QPushButton#filterBtn:hover { background: transparent; color: $text; }
QPushButton#filterBtn:checked { background: transparent; border: 1px solid transparent;
    border-bottom: 2px solid $accent; color: $text; }
QPushButton#saveBtn, QPushButton#saveBtn:disabled {
    background: transparent; border: none; color: $accent; font-family: $font_title; padding: 0 6px;
}
QPushButton#saveBtn:hover { background: $accent_soft; color: $accent_press; }
QPushButton#saveBtn:disabled { color: $faint; font-weight: 400; }
"""

# Back-compat for callers that apply the default skin directly.
QSS = build_qss(DEFAULT)
TEXT, MUTED, ACCENT = PINE["text"], PINE["muted"], PINE["accent"]
