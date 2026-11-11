"""程序化生成应用图标（无需图片资源文件）。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)


def make_icon(size: int = 64) -> QIcon:
    """绘制圆角渐变底 + 「记」字的图标。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 圆角渐变背景（暖橙 -> 砖红，类似便利贴）
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#FFB25E"))
    grad.setColorAt(1.0, QColor("#E4572E"))
    path = QPainterPath()
    path.addRoundedRect(0.5, 0.5, size - 1, size - 1, size * 0.22, size * 0.22)
    p.fillPath(path, grad)

    # 「记」字
    p.setPen(QColor("#FFFFFF"))
    font = QFont("Microsoft YaHei UI", int(size * 0.54), QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "记")
    p.end()
    return QIcon(pm)
