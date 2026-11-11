# -*- coding: utf-8 -*-
"""系统排查所有按钮 hover 是否生效。"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication, QPushButton, QToolButton  # noqa: E402
from PySide6.QtCore import Qt, QTimer, QPointF, QEvent  # noqa: E402
from PySide6.QtGui import QImage, QColor, QEnterEvent  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_shots")


def settle(app, ms=50):
    app.processEvents()
    QTimer.singleShot(ms, app.quit)
    app.exec()
    app.processEvents()


def to_img(pix):
    return pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)


def px(img, x, y):
    return QColor(img.pixel(x, y))


def sig(img):
    cnt = Counter()
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = px(img, x, y)
            cnt[(c.red(), c.green(), c.blue())] += 1
    return cnt.most_common(6)


def hover(widget, app):
    r = widget.rect()
    local = QPointF(r.center())
    scene = widget.mapTo(widget.window(), r.center())
    glob = widget.mapToGlobal(r.center())
    ev = QEnterEvent(local, QPointF(scene), QPointF(glob))
    QApplication.sendEvent(widget, ev)
    app.processEvents()
    # 再发一个 hoverMove/enter 到 window？试试补发 Enter 到 window
    return widget.underMouse()


def unhover_all(app):
    # 给所有 widget 发 leave
    for w in app.allWidgets():
        QApplication.sendEvent(w, QEvent(QEvent.Type.Leave))
    app.processEvents()


def check(app, btn, name):
    unhover_all(app)
    settle(app, 20)
    before = sig(to_img(btn.grab()))
    um = hover(btn, app)
    settle(app, 30)
    after = sig(to_img(btn.grab()))
    changed = before != after
    print(f"{name:28s} underMouse={um} changed={changed}")
    print(f"    before: {before[:3]}")
    print(f"    after : {after[:3]}")
    if changed:
        btn.grab().save(os.path.join(OUT, f"hov_{name}.png"))


def main():
    app = QApplication(sys.argv)
    from memo_app import db
    from memo_app.app import MainWindow, CategoryRow, NameDialog
    from memo_app.category_dialog import CategoryPickerDialog

    os.environ["MEMO_APP_DATA_DIR"] = "/tmp/memo_audit_full"
    win = MainWindow()
    win.resize(900, 760)
    win.show()
    settle(app)

    w0 = win.list.itemWidget(win.list.item(0))

    check(app, win.titlebar.edit_nav, "navBtn")
    check(app, win.titlebar.min_btn, "minBtn")
    check(app, win.tag_btn, "tagBtn")
    check(app, win.add_cat_btn, "addCatBtn")
    check(app, w0.done_btn, "doneBtn")
    check(app, w0.del_btn, "memoDelBtn")
    check(app, win.purge_btn, "purgeBtn(hidden)")

    rows = win.findChildren(CategoryRow)
    check(app, rows[1].del_btn, "catDelBtn")
    check(app, rows[1].arrow, "catArrow")

    # 对话框里的按钮
    dlg = NameDialog("x", "y", win)
    dlg.show()
    settle(app)
    check(app, dlg.findChildren(QPushButton)[0], "dlgCancel")
    check(app, dlg.findChildren(QPushButton)[1], "dlgOK")
    dlg.close()


if __name__ == "__main__":
    main()
