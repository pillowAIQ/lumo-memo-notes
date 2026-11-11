# -*- coding: utf-8 -*-
"""用 QEnterEvent 手动触发 hover 验证 QSS :hover 渲染。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt, QTimer, QPointF  # noqa: E402
from PySide6.QtGui import QImage, QColor, QEnterEvent  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_shots")


def settle(app, ms=80):
    app.processEvents()
    QTimer.singleShot(ms, app.quit)
    app.exec()
    app.processEvents()


def to_img(pix):
    return pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)


def px(img, x, y):
    return QColor(img.pixel(x, y))


def hover(widget, app):
    """手动投递 Enter 事件模拟 hover。"""
    r = widget.rect()
    local = QPointF(r.center())
    scene = widget.mapTo(widget.window(), r.center())
    glob = widget.mapToGlobal(r.center())
    ev = QEnterEvent(local, QPointF(scene), QPointF(glob))
    QApplication.sendEvent(widget, ev)
    app.processEvents()
    return widget.underMouse()


def unhover(widget, app):
    ev = QEnterEvent(QPointF(-100, -100), QPointF(-100, -100), QPointF(-100, -100))
    # Leave 事件
    from PySide6.QtCore import QEvent
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))
    app.processEvents()


def main():
    app = QApplication(sys.argv)
    from memo_app import db
    from memo_app.app import MainWindow, CategoryRow

    os.environ["MEMO_APP_DATA_DIR"] = "/tmp/memo_audit_full"
    win = MainWindow()
    win.resize(900, 760)
    win.show()
    settle(app)

    w0 = win.list.itemWidget(win.list.item(0))
    um = hover(w0, app)
    print("memoItem underMouse after enter:", um)
    settle(app, 30)
    print("  still:", w0.underMouse())
    shot = to_img(win.grab())
    # 在整窗图上找卡片区域：卡片 x 范围先通过 mapTo
    g = w0.mapTo(win, QPointF(0, 0)) if False else None
    from PySide6.QtCore import QPoint
    tl = w0.mapTo(win, QPoint(0, 0))
    # 边框左上角
    for dx, dy in [(0, 0), (5, 0), (0, 5), (w0.width() - 1, w0.height() // 2)]:
        c = px(shot, tl.x() + dx, tl.y() + dy)
        print(f"  edge ({dx},{dy}): #{c.red():02x}{c.green():02x}{c.blue():02x}")
    shot.save(os.path.join(OUT, "07b_card_hover.png"))
    unhover(w0, app)

    # navBtn hover
    tb = win.titlebar
    um = hover(tb.edit_nav, app)
    print("navBtn underMouse:", um)
    settle(app, 30)
    shot = to_img(tb.edit_nav.grab())
    w = tb.edit_nav.width(); h = tb.edit_nav.height()
    c = px(shot, w // 2, h // 2)
    print("  navBtn center:", c.name())
    # 找背景主色
    from collections import Counter
    cnt = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            cc = px(shot, x, y)
            cnt[(cc.red(), cc.green(), cc.blue())] += 1
    print("  top colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(5)])
    shot.save(os.path.join(OUT, "04b_nav_hover.png"))
    unhover(tb.edit_nav, app)

    # closeBtn hover -> 应 #e81123
    close_btn = tb.findChild(type(tb.min_btn), "closeBtn")
    um = hover(close_btn, app)
    print("closeBtn underMouse:", um)
    settle(app, 30)
    shot = to_img(close_btn.grab())
    w = close_btn.width(); h = close_btn.height()
    c = px(shot, w // 2, h // 2)
    print("  closeBtn center:", c.name())
    cnt = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            cc = px(shot, x, y)
            cnt[(cc.red(), cc.green(), cc.blue())] += 1
    print("  top colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(5)])
    shot.save(os.path.join(OUT, "06b_close_hover.png"))
    unhover(close_btn, app)

    # winBtn(最大化) hover
    um = hover(tb.max_btn, app)
    print("maxBtn underMouse:", um)
    settle(app, 30)
    shot = to_img(tb.max_btn.grab())
    cnt = Counter()
    w = tb.max_btn.width(); h = tb.max_btn.height()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            cc = px(shot, x, y)
            cnt[(cc.red(), cc.green(), cc.blue())] += 1
    print("  maxBtn top colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(5)])
    shot.save(os.path.join(OUT, "05b_max_hover.png"))
    unhover(tb.max_btn, app)

    # catRow hover
    rows = win.findChildren(CategoryRow)
    r = rows[1]
    um = hover(r, app)
    print("catRow underMouse:", um)
    settle(app, 30)
    shot = to_img(r.grab())
    cnt = Counter()
    for y in range(0, r.height(), 2):
        for x in range(0, r.width(), 2):
            cc = px(shot, x, y)
            cnt[(cc.red(), cc.green(), cc.blue())] += 1
    print("  catRow top colors:", [(f"#{r2:02x}{g:02x}{b:02x}", n) for (r2, g, b), n in cnt.most_common(5)])
    shot.save(os.path.join(OUT, "08_catrow_hover.png"))
    unhover(r, app)

    # done_btn hover -> 边框变绿 #2f9e63
    d = w0.done_btn
    um = hover(d, app)
    print("doneBtn underMouse:", um)
    settle(app, 30)
    shot = to_img(d.grab())
    cnt = Counter()
    for y in range(0, d.height(), 2):
        for x in range(0, d.width(), 2):
            cc = px(shot, x, y)
            cnt[(cc.red(), cc.green(), cc.blue())] += 1
    print("  doneBtn top colors:", [(f"#{r2:02x}{g:02x}{b:02x}", n) for (r2, g, b), n in cnt.most_common(6)])
    shot.save(os.path.join(OUT, "09_done_hover.png"))

    # tagBtn hover -> border #d96c3f
    um = hover(win.tag_btn, app)
    print("tagBtn underMouse:", um)
    settle(app, 30)
    shot = to_img(win.tag_btn.grab())
    cnt = Counter()
    for y in range(0, win.tag_btn.height(), 2):
        for x in range(0, win.tag_btn.width(), 2):
            cc = px(shot, x, y)
            cnt[(cc.red(), cc.green(), cc.blue())] += 1
    print("  tagBtn top colors:", [(f"#{r2:02x}{g:02x}{b:02x}", n) for (r2, g, b), n in cnt.most_common(6)])
    shot.save(os.path.join(OUT, "11_tag_hover.png"))


if __name__ == "__main__":
    main()
