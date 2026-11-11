# -*- coding: utf-8 -*-
"""补充：菜单渲染、编辑态、选中态、emoji/标签/完成态颜色。"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication, QMenu, QCheckBox, QPushButton  # noqa: E402
from PySide6.QtCore import Qt, QTimer, QPoint  # noqa: E402
from PySide6.QtGui import QImage, QColor  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_shots")


def settle(app, ms=80):
    app.processEvents()
    QTimer.singleShot(ms, app.quit)
    app.exec()
    app.processEvents()


def to_img(pix):
    return pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)


def count_colors(img, region=None):
    cnt = Counter()
    x0, y0, x1, y1 = region or (0, 0, img.width(), img.height())
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = QColor(img.pixel(x, y))
            if c.alpha() > 100:
                cnt[(c.red(), c.green(), c.blue())] += 1
    return cnt


def main():
    app = QApplication(sys.argv)
    from memo_app import db
    from memo_app.app import MainWindow, MemoItemWidget
    from memo_app.category_dialog import CategoryPickerDialog

    os.environ["MEMO_APP_DATA_DIR"] = "/tmp/memo_audit_full"
    win = MainWindow()
    win.resize(900, 500)
    win.show()
    settle(app)

    # ============ 编辑菜单（grab 隐藏也能渲染） ============
    menu = win.titlebar.edit_nav._menu
    menu.setStyleSheet(win.styleSheet())
    # 确保 polish
    app.setActiveWindow(win)
    settle(app, 30)
    shot = to_img(menu.grab())
    shot.save(os.path.join(OUT, "60_edit_menu.png"))
    print("menu grab:", shot.width(), "x", shot.height(), "null?", shot.isNull())
    if not shot.isNull():
        cnt = count_colors(shot)
        print("menu colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])

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

    # ============ pin emoji 颜色 ============
    pins = []
    for i in range(win.list.count()):
        wi = win.list.itemWidget(win.list.item(i))
        if isinstance(wi, MemoItemWidget) and wi.pin_label.isVisible():
            pins.append((i, wi))
    print("pinned items:", [i for i, _ in pins])
    if pins:
        i, wi = pins[0]
        shot = to_img(wi.pin_label.grab())
        cnt = count_colors(shot)
        print("pin emoji colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
        shot.save(os.path.join(OUT, "53_pin_emoji.png"))

    # ============ 标签颜色 ============
    for i in range(win.list.count()):
        wi = win.list.itemWidget(win.list.item(i))
        if isinstance(wi, MemoItemWidget) and wi.tags_label.isVisible():
            shot = to_img(wi.tags_label.grab())
            cnt = count_colors(shot)
            print(f"tags colors (item {i}):", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
            shot.save(os.path.join(OUT, "54_tags.png"))
            break

    # ============ done 状态按钮 ============
    for i in range(win.list.count()):
        wi = win.list.itemWidget(win.list.item(i))
        if isinstance(wi, MemoItemWidget) and wi.memo.is_done:
            shot = to_img(wi.done_btn.grab())
            cnt = count_colors(shot)
            print(f"doneBtn(done) colors (item {i}):", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(8)])
            shot.save(os.path.join(OUT, "55_done_state.png"))
            break

    # ============ 内容文字颜色（done vs normal） ============
    for i in range(min(6, win.list.count())):
        wi = win.list.itemWidget(win.list.item(i))
        if isinstance(wi, MemoItemWidget):
            shot = to_img(wi.content_label.grab())
            cnt = count_colors(shot)
            top = [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(3)]
            print(f"content item{i} done={wi.memo.is_done}: {top}")

    # ============ checkbox ============
    cats = db.list_categories()
    cpd = CategoryPickerDialog(cats, {2}, win)
    cpd.show()
    settle(app, 60)
    cbs = cpd.findChildren(QCheckBox)
    print("checkboxes:", len(cbs))
    for idx, cb in enumerate(cbs):
        shot = to_img(cb.grab())
        # indicator 在左侧 ~3-18px
        cnt = Counter()
        for y in range(shot.height()):
            for x in range(0, 22):
                c = QColor(shot.pixel(x, y))
                if c.alpha() > 100:
                    cnt[(c.red(), c.green(), c.blue())] += 1
        print(f"  cb{idx} checked={cb.isChecked()}:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(6)])
        if idx == 1:
            shot.save(os.path.join(OUT, "56_checkbox.png"))
    cpd.close()

    # ============ tagBtn emoji ============
    shot = to_img(win.tag_btn.grab())
    cnt = count_colors(shot)
    print("tagBtn colors:", [(f"#{r:02x}{g:02x}{b:02x}", n) for (r, g, b), n in cnt.most_common(10)])
    shot.save(os.path.join(OUT, "57_tagbtn.png"))

    print("done")


if __name__ == "__main__":
    main()
