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


BRAND = ("#3F8C6D", "#245845")  # 轻笺 Lumo 品牌松绿：托盘/任务栏图标固定使用


def paint_mark(p: QPainter, size: float, top: str = BRAND[0], bottom: str = BRAND[1]) -> None:
    """轻笺 · Lumo 标志：圆角方块上一张折角便笺 + 一笔对勾（32 单位网格）。"""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 32, size / 32)
    grad = QLinearGradient(0, 0, 0, 32)
    grad.setColorAt(0.0, QColor(top))
    grad.setColorAt(1.0, QColor(bottom))
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0.5, 0.5, 31, 31), 8, 8)
    p.fillPath(tile, grad)
    # 顶部一道极淡的高光，避免纯平色块显得廉价
    gloss = QLinearGradient(0, 0, 0, 16)
    gloss.setColorAt(0.0, QColor(255, 255, 255, 46))
    gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(tile, gloss)

    sheet = QPainterPath()
    sheet.moveTo(11, 7)
    sheet.lineTo(19.2, 7)
    sheet.lineTo(24.5, 12.3)
    sheet.lineTo(24.5, 22.8)
    sheet.quadTo(24.5, 25.5, 21.8, 25.5)
    sheet.lineTo(11, 25.5)
    sheet.quadTo(8.3, 25.5, 8.3, 22.8)
    sheet.lineTo(8.3, 9.7)
    sheet.quadTo(8.3, 7, 11, 7)
    p.fillPath(sheet, QColor(255, 255, 255, 246))

    fold = QPainterPath()
    fold.moveTo(19.2, 7)
    fold.lineTo(19.2, 10.6)
    fold.quadTo(19.2, 12.3, 20.9, 12.3)
    fold.lineTo(24.5, 12.3)
    fold.closeSubpath()
    fold_color = QColor(bottom)
    fold_color.setAlpha(70)
    p.fillPath(fold, QColor("#FFFFFF"))
    p.fillPath(fold, fold_color)

    pen = QPen(QColor(bottom), 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    check = QPainterPath()
    check.moveTo(12.2, 17.2)
    check.lineTo(15.1, 20.1)
    check.lineTo(20.8, 14.2)
    p.drawPath(check)
    p.restore()


def mark_pixmap(size: int, top: str = BRAND[0], bottom: str = BRAND[1], dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_mark(p, size * dpr, top, bottom)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def make_icon(size: int = 64) -> QIcon:
    """应用 / 托盘图标（固定品牌色，多分辨率）。"""
    icon = QIcon()
    for px in sorted({16, 20, 24, 32, 48, 64, 128, 256, size}):
        icon.addPixmap(mark_pixmap(px, dpr=1.0))
    return icon


def write_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    """生成多尺寸 .ico（PNG 内嵌格式，Windows Vista+ 支持），供 PyInstaller 使用。"""
    import struct

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    blobs = []
    for px in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        mark_pixmap(px, dpr=1.0).toImage().save(buf, "PNG")
        buf.close()
        blobs.append((px, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, payload = b"", b""
    for px, blob in blobs:
        dim = 0 if px >= 256 else px
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset + len(payload))
        payload += blob
    with open(path, "wb") as f:
        f.write(header + entries + payload)
