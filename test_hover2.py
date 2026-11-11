# -*- coding: utf-8 -*-
"""精确检查 done_btn / tagBtn hover 边框色。"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt, QTimer, QPointF, QEvent, QPoint  # noqa: E402
from PySide6.QtGui import QImage, QColor, QEnterEvent  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_shots")


def settle(app, ms=60):
    app.processEvents()
    QTimer.singleShot(ms, app.quit)
    app.exec()
    app.processEvents()


def to_img(pix):
    return pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)


def px(img, x, y):
    return QColor(img.pixel(x, y))


def hover(widget, app):
    r = widget.rect()
    local = QPointF(r.center())
    scene = widget.mapTo(widget.window(), r.center())
    glob = widget.mapToGlobal(r.center())
    ev = QEnterEvent(local, QPointF(scene), QPointF(glob))
    QApplication.sendEvent(widget, ev)
    app.processEvents()
    return widget.underMouse()


def border_colors(img, w, h):
    """收集控件四周 2px 边框带的颜色。"""
    cnt = Counter()
    for y in range(h):
        for x in range(w):
            edge = x < 3 or y < 3 or x >= w - 3 or y >= h - 3
            if not edge:
                continue
            c = px(img, x, y)
            cnt[(c.red(), c.green(), c.blue(), c.alpha())] += 1
    return cnt


def report(name, img, w, h):
    cnt = border_colors(img, w, h)
    total = sum(cnt.values())
    print(f"{name}: border px={total}")
    for (r, g, b, a), n in cnt.most_common(8):
        print(f"    #{r:02x}{g:02x}{b:02x} a={a}  {n}")
    # 检查目标色
    for target, label in [((47, 158, 99), "green#2f9e63"), ((217, 108, 63), "accent#d96c3f"), ((216, 69, 65), "red#d64541")]:
        hit = sum(n for (r, g, b, a), n in cnt.items()
                  if abs(r - target[0]) < 20 and abs(g - target[1]) < 20 and abs(b - target[2]) < 20)
        print(f"    ~{label}: {hit}px")


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
    d = w0.done_btn

    # 非 hover 基线
    shot = to_img(d.grab())
    report("doneBtn NORMAL", shot, d.width(), d.height())
    shot.save(os.path.join(OUT, "09a_done_normal.png"))

    um = hover(d, app)
    settle(app, 30)
    print("doneBtn underMouse:", um)
    shot = to_img(d.grab())
    report("doneBtn HOVER", shot, d.width(), d.height())
    shot.save(os.path.join(OUT, "09_done_hover.png"))

    # tagBtn
    t = win.tag_btn
    shot = to_img(t.grab())
    report("tagBtn NORMAL", shot, t.width(), t.height())
    shot.save(os.path.join(OUT, "11a_tag_normal.png"))

    um = hover(t, app)
    settle(app, 30)
    print("tagBtn underMouse:", um)
    shot = to_img(t.grab())
    report("tagBtn HOVER", shot, t.width(), t.height())
    shot.save(os.path.join(OUT, "11_tag_hover.png"))

    # catDel hover (红色 ×)
    from memo_app.app import CategoryRow
    rows = win.findChildren(CategoryRow)
    r0 = rows[1]
    r0.del_btn.setVisible(True)
    db_btn = r0.del_btn
    shot = to_img(db_btn.grab())
    report("catDel NORMAL", shot, db_btn.width(), db_btn.height())
    um = hover(db_btn, app)
    settle(app, 30)
    print("catDel underMouse:", um)
    shot = to_img(db_btn.grab())
    report("catDel HOVER", shot, db_btn.width(), db_btn.height())
    shot.save(os.path.join(OUT, "12_catdel_hover.png"))


if __name__ == "__main__":
    main()
