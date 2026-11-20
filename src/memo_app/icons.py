"""轻笺品牌图标与界面线条图标。"""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_PATHS = {
    "note": '<rect x="5" y="3" width="14" height="18" rx="3"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    "notes": '<rect x="7" y="3" width="13" height="16" rx="2"/><path d="M4 7v12a3 3 0 0 0 3 3h9M11 8h5M11 12h5"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
    "sliders": '<path d="M4 7h7m4 0h5M4 17h3m4 0h9"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "pin": '<path d="m9 3 9 9-4 1-3 5-5-5 5-3 1-4M6 18l-3 3"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "restore": '<path d="M4 10a8 8 0 1 1 1 8M4 4v6h6"/>',
    "external": '<path d="M14 3h7v7m0-7L10 14M10 5H4v15h15v-6"/>',
    "minus": '<path d="M5 12h14"/>',
    "maximize": '<rect x="5" y="5" width="14" height="14" rx="1"/>',
    "restore_window": '<path d="M9 5V3h12v12h-2"/><rect x="3" y="9" width="12" height="12" rx="1"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "sync": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 3v5h-5"/>',
    "back": '<path d="M19 12H5m6-6-6 6 6 6"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    "gear": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M7.5 14h9"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1 0 1.6-.8 1.6-1.6 0-.6-.4-1-.4-1.6 0-.8.7-1.4 1.5-1.4H17a4 4 0 0 0 4-4c0-5.1-4-9.4-9-9.4z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10.5" cy="7" r="1"/><circle cx="15.5" cy="7.8" r="1"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "toggle": '<rect x="2.5" y="7" width="19" height="10" rx="5"/><circle cx="16.5" cy="12" r="2.5"/>',
    "cloud": '<path d="M7 18.5a4.5 4.5 0 1 1 .9-8.9 6 6 0 0 1 11.3 1.4 3.8 3.8 0 0 1-.7 7.5Z"/>',
    "widget": '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M8 9h8M8 13h5"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "bell": '<path d="M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15Z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "repeat": '<path d="m17 3 3 3-3 3"/><path d="M4 11V9a3 3 0 0 1 3-3h13"/><path d="m7 21-3-3 3-3"/><path d="M20 13v2a3 3 0 0 1-3 3H4"/>',
    "flag": '<path d="M5 21V4h11l-2 4 2 4H5"/>',
    "star": '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9Z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-4.5 3.5 3 3-2.5 4.5 4"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M15 4v16"/>',
}


@lru_cache(maxsize=128)
def ui_icon(name: str, color: str = "#69766E", size: int = 20) -> QIcon:
    """Render the same rounded, 1.6px line vocabulary at both desktop DPIs."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("ascii")))
    icon = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap)
    return icon


@lru_cache(maxsize=1)
def make_icon() -> QIcon:
    """应用、窗口和托盘共享同一个多尺寸 Windows 图标。"""
    return QIcon(str(Path(__file__).with_name("assets") / "lumo.ico"))


def mark_pixmap(size: int, dpr: float = 2.0) -> QPixmap:
    return make_icon().pixmap(QSize(size, size), dpr)
