"""程序化生成应用图标（无需图片资源文件）。"""
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
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


def make_icon(size: int = 64) -> QIcon:
    """Draw a small notebook mark without depending on installed glyphs."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#6B8C68"))
    grad.setColorAt(1.0, QColor("#2D6550"))
    path = QPainterPath()
    path.addRoundedRect(0.5, 0.5, size - 1, size - 1, size * 0.22, size * 0.22)
    p.fillPath(path, grad)

    p.scale(size / 32, size / 32)
    p.setPen(QPen(QColor("#FFFFFF"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawRoundedRect(QRectF(9, 6.5, 15, 20), 2, 2)
    p.drawLine(12, 7, 12, 26)
    p.drawLine(16, 12, 21, 12)
    p.drawLine(16, 16, 21, 16)
    p.drawLine(16, 20, 19, 20)
    p.end()
    return QIcon(pm)
