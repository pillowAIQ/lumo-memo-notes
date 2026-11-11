# -*- coding: utf-8 -*-
"""补充截图：滚动条、编辑菜单、编辑态卡片、emoji/标签颜色验证。"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication  # noqa: E402
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


def main():
    app = QApplication(sys.argv)
    from memo_app import db
    from memo_app.app import MainWindow

    os.environ["MEMO_APP_DATA_DIR"] = "/tmp/memo_audit_full"
    # 塞 30 条制造滚动条
    existing = len(db.list_memos())
    for i in range(30):
        db.add_memo(f"滚动测试备忘录第 {i+1} 条：这是一段用来撑高列表的内容")
    win = MainWindow()
    win.resize(900, 500)   # 更矮，确保出现滚动条
    win.show()
    settle(app)
    img = to_img(win.grab())
    img.save(os.path.join(OUT, "50_scrollbar.png"))
    print("scrollbar shot saved; sb max:", win.list.verticalScrollBar().maximum())

    # 分析右侧滚动条区域颜色
    w = img.width()
    cnt = Counter()
    for y in range(60, img.height() - 15, 2):
        for x in range(w - 20, w - 2):
            c = QColor(img.pixel(x, y))
            cnt[(c.red(), c.green(), c.blue())] += 1
    print("right-edge top colors:")
    for (r, g, b), n in cnt.most_common(10):
        print(f"   #{r:02x}{g:02x}{b:02x}  {n}")

    # 滚动条滑块检测：找 x=w-16 附近竖直连续非背景段
    for x in range(w - 18, w - 8):
        col = Counter()
        for y in range(60, img.height() - 15):
            c = QColor(img.pixel(x, y))
            col[(c.red(), c.green(), c.blue())] += 1
        top = col.most_common(2)
        print(f"  x={x}: {[(f'#{r:02x}{g:02x}{b:02x}', n) for (r,g,b),n in top]}")

    # ============ 编辑菜单截图 ============
    menu = win.titlebar.edit_nav._menu
    # 尝试 popup + exec 异步
    gp = win.mapToGlobal(QPoint(80, 42))
    menu.popup(gp)
    for _ in range(10):
        app.processEvents()
        QTimer.singleShot(30, app.quit)
        app.exec()
    print("menu visible:", menu.isVisible())
    if menu.isVisible():
        shot = to_img(menu.grab())
        shot.save(os.path.join(OUT, "60_edit_menu.png"))
        print("menu size:", shot.width(), shot.height())
        cnt = Counter()
        for y in range(0, shot.height(), 2):
            for x in range(0, shot.width(), 2):
                c = QColor(shot.pixel(x, y))
                cnt[(c.red(), c.green(), c.blue())] += 1
        print("menu colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
        menu.hide()
        settle(app, 30)
    else:
        # 退路：把菜单当普通 widget 渲染
        menu2 = QMenu()
        menu2.addAction("搜索备忘录")
        menu2.addAction("回收站")
        menu2.setStyleSheet(win.styleSheet())
        menu2.show()
        settle(app, 60)
        print("menu2 visible:", menu2.isVisible())
        if menu2.isVisible():
            shot = to_img(menu2.grab())
            shot.save(os.path.join(OUT, "60_edit_menu.png"))
            print("menu2 size:", shot.width(), shot.height())
        menu2.hide()

    # ============ 编辑态卡片 ============
    item = win.list.item(0)
    w0 = win.list.itemWidget(item)
    w0._start_edit()
    settle(app, 60)
    win.grab().save(os.path.join(OUT, "51_edit_state.png"))
    w0._end_edit(save=False)
    settle(app, 40)

    # ============ 选中卡片态 ============
    win.list.setCurrentRow(0)
    settle(app, 60)
    win.grab().save(os.path.join(OUT, "52_selected_card.png"))
    win.list.clearSelection()
    settle(app, 40)

    # ============ emoji/标签颜色探测 ============
    # 找置顶卡片（第一条是置顶的"买牛奶和鸡蛋"，但现在第一条是新加的…）
    # 直接找 pin_label 有可见的 widget
    from memo_app.app import MemoItemWidget
    pins = []
    for i in range(win.list.count()):
        wi = win.list.itemWidget(win.list.item(i))
        if isinstance(wi, MemoItemWidget) and wi.pin_label.isVisible():
            pins.append((i, wi))
    print("pinned items:", [i for i, _ in pins])
    if pins:
        i, wi = pins[0]
        # pin_label 位置
        pl = wi.pin_label
        shot = to_img(pl.grab())
        cnt = Counter()
        for y in range(shot.height()):
            for x in range(shot.width()):
                c = QColor(shot.pixel(x, y))
                if c.alpha() > 100:
                    cnt[(c.red(), c.green(), c.blue())] += 1
        print("pin emoji colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
        shot.save(os.path.join(OUT, "53_pin_emoji.png"))

    # 标签颜色
    tags = []
    for i in range(win.list.count()):
        wi = win.list.itemWidget(win.list.item(i))
        if isinstance(wi, MemoItemWidget) and wi.tags_label.isVisible():
            tags.append((i, wi))
    if tags:
        i, wi = tags[0]
        shot = to_img(wi.tags_label.grab())
        cnt = Counter()
        for y in range(shot.height()):
            for x in range(shot.width()):
                c = QColor(shot.pixel(x, y))
                if c.alpha() > 100:
                    cnt[(c.red(), c.green(), c.blue())] += 1
        print("tags colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
        shot.save(os.path.join(OUT, "54_tags.png"))

    # done 按钮已完成态
    dones = []
    for i in range(win.list.count()):
        wi = win.list.itemWidget(win.list.item(i))
        if isinstance(wi, MemoItemWidget) and wi.memo.is_done:
            dones.append((i, wi))
    if dones:
        i, wi = dones[0]
        shot = to_img(wi.done_btn.grab())
        cnt = Counter()
        for y in range(shot.height()):
            for x in range(shot.width()):
                c = QColor(shot.pixel(x, y))
                if c.alpha() > 100:
                    cnt[(c.red(), c.green(), c.blue())] += 1
        print("doneBtn(done) colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
        shot.save(os.path.join(OUT, "55_done_state.png"))

    # checkbox checked (category picker)
    from memo_app.category_dialog import CategoryPickerDialog
    cats = db.list_categories()
    cpd = CategoryPickerDialog(cats, {2}, win)
    cpd.show()
    settle(app, 60)
    cbs = cpd.findChildren(type(cpd.findChildren(__import__('PySide6.QtWidgets', fromlist=['QCheckBox']).QCheckBox)[0])) if cpd.findChildren(__import__('PySide6.QtWidgets', fromlist=['QCheckBox']).QCheckBox) else []
    from PySide6.QtWidgets import QCheckBox
    cbs = cpd.findChildren(QCheckBox)
    if len(cbs) >= 2:
        shot = to_img(cbs[1].grab())
        shot.save(os.path.join(OUT, "56_checkbox.png"))
        # 找 indicator 区域颜色（左侧 ~15px）
        cnt = Counter()
        for y in range(shot.height()):
            for x in range(0, 20):
                c = QColor(shot.pixel(x, y))
                if c.alpha() > 100:
                    cnt[(c.red(), c.green(), c.blue())] += 1
        print("checkbox(checked) indicator colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
    cpd.close()

    print("done")


if __name__ == "__main__":
    main()
