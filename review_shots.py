# -*- coding: utf-8 -*-
"""UI 审查截图脚本：offscreen 渲染各界面并保存 PNG。"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 必须先 import memo_app（Windows DLL 预加载 workaround），再 import PySide6
import memo_app  # noqa: F401

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_shots")
os.makedirs(OUT, exist_ok=True)

DATA_FULL = os.path.join(tempfile.gettempdir(), "memo_review_full")
DATA_EMPTY = os.path.join(tempfile.gettempdir(), "memo_review_empty")

LONG_TEXT = (
    "这是一条很长的备忘录，用来测试卡片流在单行省略模式下的表现。"
    "它包含了中文、English words 和一些标点符号，看看省略号截断是否美观，"
    "以及 tooltip 是否能显示完整内容。顺便再加长一点，确保它一定会超出可视宽度。"
    "尾部内容应该被截断。"
)
SPECIAL = "含特殊字符 %s_\\ 测试 LIKE ESCAPE 搜索 100%_done"


def seed(db):
    os.environ["MEMO_APP_DATA_DIR"] = DATA_FULL
    db.init_db()
    ids = []
    ids.append(db.add_memo("买牛奶和鸡蛋"))
    ids.append(db.add_memo(LONG_TEXT))
    ids.append(db.add_memo(SPECIAL))
    ids.append(db.add_memo("给妈妈打电话，别忘了"))
    ids.append(db.add_memo("周五前交季度报告初稿"))
    ids.append(db.add_memo("已完成的事项示例：整理桌面"))
    db.toggle_done(ids[-1])
    ids.append(db.add_memo("再一条已完成：回复邮件"))
    db.toggle_done(ids[-1])
    db.toggle_pin(ids[0])  # 置顶第一条
    db.soft_delete(ids[3])  # 进回收站
    db.soft_delete(ids[4])
    return ids


def settle(app, ms=120):
    app.processEvents()
    QTimer.singleShot(ms, app.quit)
    app.exec()
    app.processEvents()


def grab(widget, name):
    pix = widget.grab()
    path = os.path.join(OUT, name)
    pix.save(path)
    print("saved:", path, pix.size().width(), "x", pix.size().height())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("memo")
    app.setOrganizationName("memo_app")

    from memo_app import db
    from memo_app.app import MainWindow
    from memo_app.widget import TodoWidget
    from memo_app.capture import QuickCapture

    seed(db)

    # ---------- 1. 主窗口正常态 ----------
    os.environ["MEMO_APP_DATA_DIR"] = DATA_FULL
    win = MainWindow()
    win.resize(580, 800)
    win.show()
    settle(app)
    grab(win, "01_main_with_data.png")

    # ---------- 4. 编辑状态 ----------
    item = win.list.item(1)
    w = win.list.itemWidget(item)
    if w is not None:
        w._start_edit()
        settle(app, 80)
        grab(win, "04_edit_state.png")
        w._end_edit(save=False)
        settle(app, 80)

    # ---------- 3. 回收站视图 ----------
    win._set_trash(True)
    settle(app)
    grab(win, "03_trash_view.png")
    win._set_trash(False)
    settle(app)

    # ---------- 2. 主窗口空态 ----------
    os.environ["MEMO_APP_DATA_DIR"] = DATA_EMPTY
    win2 = MainWindow()
    win2.resize(580, 800)
    win2.show()
    settle(app)
    grab(win2, "02_main_empty.png")

    # ---------- 5. 桌面待办挂件 ----------
    os.environ["MEMO_APP_DATA_DIR"] = DATA_FULL
    tw = TodoWidget()
    tw.reload()
    tw.show()
    settle(app)
    grab(tw, "05_todo_widget.png")
    # 空挂件状态
    os.environ["MEMO_APP_DATA_DIR"] = DATA_EMPTY
    tw2 = TodoWidget()
    tw2.reload()
    tw2.show()
    settle(app)
    grab(tw2, "05b_todo_widget_empty.png")

    # ---------- 6. QuickCapture 快捷输入 ----------
    qc = QuickCapture()
    qc.present()
    settle(app, 80)
    grab(qc, "06_quick_capture.png")

    # 搜索状态（有匹配）
    os.environ["MEMO_APP_DATA_DIR"] = DATA_FULL
    win3 = MainWindow()
    win3.resize(580, 800)
    win3.search_edit.setText("牛奶")
    win3.show()
    settle(app)
    grab(win3, "07_search_result.png")

    print("done ->", OUT)


if __name__ == "__main__":
    main()
