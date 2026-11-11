# -*- coding: utf-8 -*-
"""几何分析：卡片行高/间距/对齐、侧边栏、菜单、滚动条。"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QEvent  # noqa: E402
from PySide6.QtGui import QImage, QColor, QEnterEvent, QMouseEvent  # noqa: E402

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


def is_whiteish(c, tol=8):
    return c.red() > 250 and c.green() > 250 and c.blue() > 250 and c.alpha() > 200


def card_geometry(img, x_range=(200, 870)):
    """按行统计白色像素数，找出卡片纵向区间。"""
    rows = []
    for y in range(img.height()):
        cnt = 0
        for x in range(x_range[0], x_range[1], 2):
            if is_whiteish(px(img, x, y)):
                cnt += 1
        rows.append(cnt)
    # 卡片行：白像素数 > 100（总宽 670/2=335 采样点）
    in_card = [c > 80 for c in rows]
    spans = []
    y = 0
    while y < len(in_card):
        if in_card[y]:
            y0 = y
            while y < len(in_card) and in_card[y]:
                y += 1
            spans.append((y0, y - 1))
        else:
            y += 1
    return spans


def card_xrange(img, y):
    """在给定 y 行找卡片左右边缘（白色区域）。"""
    xs = [x for x in range(150, 899) if is_whiteish(px(img, x, y))]
    if not xs:
        return None
    # 取最长连续段
    best = (xs[0], xs[0])
    cur = (xs[0], xs[0])
    for x in xs[1:]:
        if x - cur[1] <= 3:
            cur = (cur[0], x)
        else:
            if cur[1] - cur[0] > best[1] - best[0]:
                best = cur
            cur = (x, x)
    if cur[1] - cur[0] > best[1] - best[0]:
        best = cur
    return best


def main():
    app = QApplication(sys.argv)
    from memo_app import db
    from memo_app.app import MainWindow

    os.environ["MEMO_APP_DATA_DIR"] = "/tmp/memo_audit_full"
    win = MainWindow()
    win.resize(900, 760)
    win.show()
    settle(app)

    img = to_img(win.grab())
    spans = card_geometry(img)
    print("card spans (y0-y1, height):")
    prev_end = None
    for y0, y1 in spans:
        gap = y0 - prev_end - 1 if prev_end is not None else None
        xr = card_xrange(img, (y0 + y1) // 2)
        print(f"  y {y0}-{y1} h={y1-y0+1} gap_before={gap} x={xr}")
        prev_end = y1

    # 卡片边框色采样：第一张卡片上边框中点
    if spans:
        y0, y1 = spans[0]
        xr = card_xrange(img, (y0 + y1) // 2)
        if xr:
            midx = (xr[0] + xr[1]) // 2
            for dy in (-1, 0, 1, y1 - y0, y1 - y0 + 1):
                c = px(img, midx, y0 + dy)
                print(f"  top-border probe y={y0+dy}: #{c.red():02x}{c.green():02x}{c.blue():02x}")
            # 左右边缘
            for dx in (-1, 0, 1):
                c = px(img, xr[0] + dx, (y0 + y1) // 2)
                print(f"  left-border probe x={xr[0]+dx}: #{c.red():02x}{c.green():02x}{c.blue():02x}")

    # 侧边栏行高/位置
    print("\nsidebar probe:")
    # 在 x=100 找分类行背景 #efede6 vs 行 hover/selected
    # selected 行（全部）应 #fbeee5
    for y in range(60, 140):
        c = px(img, 100, y)
        if c.green() > 230 and c.red() > 230:
            pass
    # 直接报告几个 y 的颜色
    for y in (62, 70, 80, 92, 104, 116, 128):
        c = px(img, 100, y)
        print(f"  sidebar x=100 y={y}: #{c.red():02x}{c.green():02x}{c.blue():02x}")

    # ============ 编辑菜单弹出 ============
    menu = win.titlebar.edit_nav._menu
    menu.popup(win.mapToGlobal(QPoint(60, 40)))
    settle(app, 100)
    if menu.isVisible():
        shot = to_img(menu.grab())
        shot.save(os.path.join(OUT, "60_edit_menu.png"))
        print("menu shot saved", shot.width(), shot.height())
        # 菜单项分析
        cnt = Counter()
        for y in range(0, shot.height(), 2):
            for x in range(0, shot.width(), 2):
                c = QColor(shot.pixel(x, y))
                cnt[(c.red(), c.green(), c.blue())] += 1
        print("  menu top colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
        menu.hide()
        settle(app, 30)
    else:
        print("menu not visible after popup")

    # ============ 滚动条截图分析（之前 test_hover 塞了40条） ============
    p = os.path.join(OUT, "50_scrollbar.png")
    if os.path.exists(p):
        img2 = QImage(p).convertToFormat(QImage.Format.Format_ARGB32)
        print(f"\nscrollbar shot {img2.width()}x{img2.height()}")
        # 右侧区域颜色统计
        cnt = Counter()
        for y in range(60, img2.height() - 20, 3):
            for x in range(img2.width() - 22, img2.width() - 4):
                c = QColor(img2.pixel(x, y))
                cnt[(c.red(), c.green(), c.blue())] += 1
        print("  right-edge colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(10)])


if __name__ == "__main__":
    main()
