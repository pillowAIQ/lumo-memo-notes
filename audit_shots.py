# -*- coding: utf-8 -*-
"""UI 审查截图脚本：offscreen 渲染所有界面，保存到 audit_shots/。

用法（MSYS bash）：
    MEMO_APP_DATA_DIR=/tmp/memo_audit QT_QPA_PLATFORM=offscreen \
        .venv/Scripts/python.exe audit_shots.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import memo_app  # noqa: F401  (Windows DLL 预加载 workaround)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "audit_shots")
os.makedirs(OUT, exist_ok=True)

DATA_FULL = "/tmp/memo_audit_full"
DATA_EMPTY = "/tmp/memo_audit_empty"

LONG_TEXT = (
    "这是一条很长的备忘录，用来测试卡片流在单行省略模式下的表现。"
    "它包含了中文、English words 和一些标点符号，看看省略号截断是否美观，"
    "以及 tooltip 是否能显示完整内容。顺便再加长一点，确保它一定会超出可视宽度。"
    "尾部内容应该被截断。"
)


def settle(app, ms=120):
    app.processEvents()
    QTimer.singleShot(ms, app.quit)
    app.exec()
    app.processEvents()


def grab(widget, name):
    pix = widget.grab()
    path = os.path.join(OUT, name)
    pix.save(path)
    print("saved:", path, pix.width(), "x", pix.height())


class StubCtl:
    """SettingsDialog 只需要这三个方法。"""

    def hotkey_display(self):
        return "Ctrl+O"

    def describe_pressed(self):
        return ""

    def set_hotkey(self, spec):
        return True


def seed(db):
    os.environ["MEMO_APP_DATA_DIR"] = DATA_FULL
    import shutil
    shutil.rmtree(DATA_FULL, ignore_errors=True)
    db.init_db()

    # 分类
    db.create_category("工作")
    db.create_category("生活")
    db.create_category("购物清单")

    ids = []
    ids.append(db.add_memo("买牛奶和鸡蛋"))
    db.set_memo_categories(ids[-1], [2, 3])
    ids.append(db.add_memo(LONG_TEXT))
    ids.append(db.add_memo("给妈妈打电话，别忘了"))
    db.set_memo_categories(ids[-1], [2])
    ids.append(db.add_memo("周五前交季度报告初稿"))
    db.set_memo_categories(ids[-1], [1])
    ids.append(db.add_memo("已完成：整理桌面"))
    db.toggle_done(ids[-1])
    db.set_memo_categories(ids[-1], [2])
    ids.append(db.add_memo("再一条已完成：回复邮件"))
    db.toggle_done(ids[-1])
    ids.append(db.add_memo("去超市买酱油和醋"))
    db.set_memo_categories(ids[-1], [3])
    db.toggle_pin(ids[0])  # 置顶
    db.soft_delete(ids[3])  # 回收站
    db.soft_delete(ids[5])
    return ids


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("memo")
    app.setOrganizationName("memo_app")

    from memo_app import db
    from memo_app.app import MainWindow, NameDialog
    from memo_app.category_dialog import CategoryPickerDialog
    from memo_app.widget import TodoWidget
    from memo_app.capture import QuickCapture
    from memo_app.settings_dialog import SettingsDialog

    seed(db)

    # ============ 1. 主窗口完整态 ============
    os.environ["MEMO_APP_DATA_DIR"] = DATA_FULL
    win = MainWindow()
    win.resize(900, 760)
    win.show()
    settle(app)
    grab(win, "01_main_full.png")

    # 分类选中态（生活）
    win.selected_category = 2
    win.reload()
    settle(app, 80)
    grab(win, "02_main_cat_selected.png")
    win.selected_category = None
    win.reload()

    # 搜索态
    win.search_edit.setText("牛奶")
    win._toggle_search(True)
    win.reload()
    settle(app, 80)
    grab(win, "03_search_state.png")
    win._toggle_search(False)
    settle(app, 80)

    # hover 状态：编辑菜单按钮 / 设置按钮 / 关闭按钮 / 卡片
    tb = win.titlebar
    QTest.mouseMove(tb.edit_nav)
    settle(app, 60)
    grab(tb, "04_titlebar_hover_nav.png")

    QTest.mouseMove(tb, tb.mapFromGlobal(tb.mapToGlobal(__import__('PySide6.QtCore', fromlist=['QPoint']).QPoint(20, 20))))
    # 直接 hover 关闭按钮
    for b in (tb.min_btn, tb.max_btn):
        QTest.mouseMove(b)
    settle(app, 60)
    grab(tb, "05_titlebar_hover_max.png")
    # hover 关闭按钮（红色 hover）
    # 找到 close 按钮：它是第三个 winBtn/closeBtn
    close_btn = tb.findChild(__import__('PySide6.QtWidgets', fromlist=['QToolButton']).QToolButton, "closeBtn")
    # findChild 不支持按 objectName 之外的方式？支持：findChild(QWidget, name) 用 objectName
    QTest.mouseMove(close_btn)
    settle(app, 60)
    grab(tb, "06_titlebar_hover_close.png")
    QTest.mouseMove(tb, __import__('PySide6.QtCore', fromlist=['QPoint']).QPoint(200, 20))

    # 卡片 hover（第一条 memoItem）
    item0 = win.list.item(0)
    w0 = win.list.itemWidget(item0)
    QTest.mouseMove(w0, w0.rect().center())
    settle(app, 60)
    grab(win, "07_card_hover.png")

    # ============ 2. 空态 ============
    os.environ["MEMO_APP_DATA_DIR"] = DATA_EMPTY
    import shutil
    shutil.rmtree(DATA_EMPTY, ignore_errors=True)
    db.init_db()
    win2 = MainWindow()
    win2.resize(900, 760)
    win2.show()
    settle(app)
    grab(win2, "10_main_empty.png")

    # 回收站空态
    win2._set_trash(True)
    settle(app, 80)
    grab(win2, "11_trash_empty.png")
    win2.close()

    # ============ 3. 回收站视图（有数据） ============
    os.environ["MEMO_APP_DATA_DIR"] = DATA_FULL
    win3 = MainWindow()
    win3.resize(900, 760)
    win3.show()
    settle(app)
    win3._set_trash(True)
    settle(app, 80)
    grab(win3, "12_trash_full.png")

    # ============ 4. 设置对话框 ============
    dlg = SettingsDialog(StubCtl(), win3)
    dlg.show()
    settle(app, 80)
    grab(dlg, "20_settings.png")
    dlg.close()

    # ============ 5. NameDialog / CategoryPickerDialog ============
    nd = NameDialog("新建分类", "分类名称：", win3)
    nd.show()
    settle(app, 80)
    grab(nd, "21_namedialog.png")
    nd.close()

    cpd = CategoryPickerDialog(db.list_categories(), {2}, win3)
    cpd.show()
    settle(app, 80)
    grab(cpd, "22_category_picker.png")
    cpd.close()

    # ============ 6. QuickCapture ============
    qc = QuickCapture()
    qc.edit.setText("这是快捷输入框里的示例文字")
    qc.present()
    settle(app, 80)
    grab(qc, "30_quick_capture.png")
    qc.hide()

    # ============ 7. TodoWidget ============
    tw = TodoWidget()
    tw.reload()
    tw.show()
    settle(app, 80)
    grab(tw, "40_todo_widget.png")

    # 挂件 hover done 按钮
    rows = []
    for i in range(tw.list_host.count()):
        it = tw.list_host.itemAt(i)
        if it and it.widget():
            rows.append(it.widget())
    if rows:
        btn = rows[0].findChild(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton)
        if btn:
            QTest.mouseMove(btn)
            settle(app, 60)
            grab(tw, "41_todo_widget_hover.png")
    tw.hide()

    # 空挂件
    os.environ["MEMO_APP_DATA_DIR"] = DATA_EMPTY
    db.init_db()
    tw2 = TodoWidget()
    tw2.reload()
    tw2.show()
    settle(app, 80)
    grab(tw2, "42_todo_widget_empty.png")
    tw2.hide()

    print("done ->", OUT)


if __name__ == "__main__":
    main()
