"""备忘录图片附件：保存 / 缩略图 / 剪贴板读取。

图片转存到数据目录的 images/ 下（db.images_dir()），以内容 sha256 命名，库里只记文件名。
过大的图片按最长边缩到 MAX_SIDE；PNG 仍超过 MAX_PNG_BYTES（照片类）改存 JPEG，
保证单张图片能在同步协议的大小上限内上传。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QImageReader, QPixmap

from . import db

MAX_SIDE = 2560
MAX_PNG_BYTES = 6 * 1024 * 1024
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
FILE_FILTER = "图片 (" + " ".join(f"*{s}" for s in IMAGE_SUFFIXES) + ")"

_thumb_cache: dict[tuple[str, int, float], QPixmap] = {}


def save_qimage(img: QImage) -> str | None:
    """把图片写入图片目录，返回文件名；空图或写入失败返回 None。"""
    if img is None or img.isNull():
        return None
    if max(img.width(), img.height()) > MAX_SIDE:
        img = img.scaled(
            MAX_SIDE, MAX_SIDE,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
    data = _encode(img, "PNG")
    ext = "png"
    if data is None or len(data) > MAX_PNG_BYTES:
        data = _encode(img.convertToFormat(QImage.Format.Format_RGB32), "JPG", 90)
        ext = "jpg"
    if not data:
        return None
    try:
        return db.store_image_bytes(data, ext)
    except OSError:
        return None


def _encode(img: QImage, fmt: str, quality: int = -1) -> bytes | None:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = img.save(buf, fmt, quality)
    buf.close()
    return bytes(ba.data()) if ok else None


def load_file(path: str | Path) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)  # 照片 EXIF 方向
    return reader.read()


def is_image_file(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def attach_image(memo_id: int, img: QImage) -> bool:
    name = save_qimage(img)
    if name is None:
        return False
    db.add_memo_image(memo_id, name)
    return True


def attach_files(memo_id: int, paths) -> int:
    """批量附加图片文件，返回成功数量。"""
    n = 0
    for p in paths:
        if attach_image(memo_id, load_file(p)):
            n += 1
    return n


def images_from_mime(md: QMimeData | None) -> list[QImage]:
    """从剪贴板 / 拖放数据里取图片：位图数据，或本地图片文件。"""
    if md is None:
        return []
    out: list[QImage] = []
    if md.hasUrls():
        for url in md.urls():
            if url.isLocalFile() and is_image_file(url.toLocalFile()):
                img = load_file(url.toLocalFile())
                if not img.isNull():
                    out.append(img)
    if not out and md.hasImage():
        data = md.imageData()
        if isinstance(data, QPixmap):
            img = data.toImage()
        elif isinstance(data, QImage):
            img = data
        else:
            img = QImage()
        if not img.isNull():
            out.append(img)
    return out


def mime_has_image(md: QMimeData | None) -> bool:
    if md is None:
        return False
    if md.hasImage():
        return True
    return md.hasUrls() and any(
        u.isLocalFile() and is_image_file(u.toLocalFile()) for u in md.urls()
    )


def clipboard_images() -> list[QImage]:
    return images_from_mime(QGuiApplication.clipboard().mimeData())


def thumbnail(filename: str, size: int, dpr: float = 2.0) -> QPixmap:
    """居中裁成正方形的缩略图（带缓存）。文件丢失时返回空 QPixmap。"""
    key = (filename, size, dpr)
    pm = _thumb_cache.get(key)
    if pm is not None:
        return pm
    px = int(size * dpr)
    reader = QImageReader(str(db.image_path(filename)))
    reader.setAutoTransform(True)
    full = reader.size()
    if full.isValid() and full.width() > 0 and full.height() > 0:
        # 先按短边缩放再读，大图也很快
        scale = px / min(full.width(), full.height())
        if scale < 1:
            reader.setScaledSize(QSize(max(1, round(full.width() * scale)), max(1, round(full.height() * scale))))
    img = reader.read()
    if img.isNull():
        return QPixmap()
    img = img.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    x = (img.width() - px) // 2
    y = (img.height() - px) // 2
    pm = QPixmap.fromImage(img.copy(x, y, px, px))
    pm.setDevicePixelRatio(dpr)
    if len(_thumb_cache) > 256:
        _thumb_cache.clear()
    _thumb_cache[key] = pm
    return pm


def open_external(filename: str) -> bool:
    """用系统默认看图程序打开。"""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(db.image_path(filename))))


def copy_to_clipboard(filename: str) -> bool:
    img = load_file(db.image_path(filename))
    if img.isNull():
        return False
    QGuiApplication.clipboard().setImage(img)
    return True
