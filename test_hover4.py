# -*- coding: utf-8 -*-
"""补发 MouseMove/HoverMove 事件试试能否触发 QPushButton :hover 重绘。"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from PySide6.QtCore import Qt, QTimer, QPointF, QEvent, QPoint  # noqa: E402
from PySide6.QtGui import QImage, QColor, QEnterEvent, QMouseEvent  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_shots")


def settle(app, ms=50):
    app.processEvents()
    QTimer.singleShot(ms, app.quit)
    app.exec()
    app.processEvents()


def to_img(pix):
    return pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)


def sig(img):
    cnt = Counter()
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = QColor(img.pixel(x, y))
            cnt[(c.red(), c.green(), c.blue())] += 1
    return cnt.most_common(5)


def try_seq(app, btn, name):
    r = btn.rect()
    c = r.center()
    # 1) Enter
    QApplication.sendEvent(btn, QEnterEvent(QPointF(c), QPointF(c), btn.mapToGlobal(c)))
    # 2) MouseMove (no buttons)
    mev = QMouseEvent(QEvent.Type.MouseMove, QPointF(c), btn.mapTo(btn.window(), c), btn.mapToGlobal(c), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(btn, mev)
    # 3) HoverMove
    hev = QMouseEvent(QEvent.Type.HoverMove, QPointF(c), btn.mapTo(btn.window(), c), btn.mapToGlobal(c), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(btn, hev)
    app.processEvents()
    settle(app, 40)
    print(f"{name}: underMouse={btn.underMouse()}")
    img = to_img(btn.grab())
    print("   ", sig(img))
    img.save(os.path.join(OUT, f"seq_{name}.png"))
    # 还原：发 leave
    QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
    app.processEvents()


def main():
    app = QApplication(sys.argv)
    from memo_app import db
    from memo_app.app import MainWindow

    os.environ["MEMO_APP_DATA_DIR"] = "/tmp/memo_audit_full"
    win = MainWindow()
    win.resize(900, 760)
    win.show()
    settle(app)
    w0 = win.list.itemWidget(win.list.item(0))
    try_seq(app, win.tag_btn, "tagBtn")
    try_seq(app, w0.done_btn, "doneBtn")
    try_seq(app, win.add_cat_btn, "addCatBtn")


if __name__ == "__main__":
    main()
