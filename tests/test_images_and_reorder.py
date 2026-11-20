"""分类拖动排序 + 备忘录图片附件（offscreen 子进程，隔离数据目录）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

_SCRIPT = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import memo_app  # noqa: F401
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from memo_app import db, images, theme
from memo_app.app import CategoryRow, MainWindow

app = QApplication(sys.argv)
theme.apply("", style_key="mica")
db.init_db()

def ok(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        os._exit(1)

# ---- 分类排序（db 层）----
a = db.create_category("工作"); b = db.create_category("生活"); c = db.create_category("学习")
ok([x.id for x in db.list_categories()] == [a, b, c], "默认按创建顺序")
ok(db.move_category(b, a, after=False), "移动返回 True")
ok([x.id for x in db.list_categories()] == [b, a, c], "生活拖到工作上面")
ok(db.move_category(b, c, after=True), "拖到末尾")
ok([x.id for x in db.list_categories()] == [a, c, b], "拖到末尾顺序")
ok(not db.move_category(a, a, after=True), "拖到自己无变化")
d = db.create_category("新分类")
ok([x.id for x in db.list_categories()] == [a, c, b, d], "新分类排在已排序之后")

# ---- 图片（db + 文件）----
mid = db.add_memo("带图的记录")
img = QImage(4000, 1000, QImage.Format.Format_RGB32); img.fill(QColor("#3366aa"))
ok(images.attach_image(mid, img), "附加图片")
rows = db.list_memo_images(mid)
ok(len(rows) == 1, "库里一条图片")
path = db.image_path(rows[0][1])
ok(path.exists(), "图片文件已写入")
saved = QImage(str(path))
ok(max(saved.width(), saved.height()) == images.MAX_SIDE, f"大图被缩小 {saved.size()}")
ok(not images.thumbnail(rows[0][1], 90).isNull(), "缩略图")
ok(db.memo_image_counts() == {mid: 1}, "图片计数")
db.delete_memo_image(rows[0][0])
ok(not path.exists() and db.list_memo_images(mid) == [], "删除图片连同文件")
images.attach_image(mid, img)
path = db.image_path(db.list_memo_images(mid)[0][1])
db.soft_delete(mid)
ok(path.exists(), "移入回收站时图片保留")
db.purge(mid)
ok(not path.exists() and db.memo_image_counts() == {}, "彻底删除时清理图片")

# ---- 主界面：侧栏排序 / 详情栏 / 输入框粘贴图片 ----
win = MainWindow(); win.resize(1200, 760); win.show(); QTest.qWait(100)

def side_ids():
    out = []
    for i in range(win.side_rows.count()):
        w = win.side_rows.itemAt(i).widget()
        if isinstance(w, CategoryRow) and w.cat_id and w.isVisible():
            out.append(w.cat_id)
    return out

ok(side_ids() == [a, c, b, d], f"侧栏顺序 {side_ids()}")
win._on_cat_move(b, a, False)
QTest.qWait(50)
ok(side_ids() == [b, a, c, d], f"侧栏拖动后 {side_ids()}")
from PySide6.QtCore import QPointF
from PySide6.QtGui import QDropEvent
def row_of(cid):
    for i in range(win.side_rows.count()):
        w = win.side_rows.itemAt(i).widget()
        if isinstance(w, CategoryRow) and w.cat_id == cid and w.isVisible():
            return w
pressed = row_of(a)
QTest.mousePress(pressed, Qt.MouseButton.LeftButton, pos=pressed.rect().center())
QTest.qWait(50)
ok(win.selected_category == a and row_of(a) is pressed and pressed.isVisible(),
   "按下未选中的分类：原地切换选中，不重建（才能继续拖动）")
QTest.mouseRelease(pressed, Qt.MouseButton.LeftButton, pos=pressed.rect().center())
drag_md = QMimeData(); drag_md.setData("application/x-lumo-category", str(c).encode())
target = row_of(b)
ev = QDropEvent(QPointF(10, 2), Qt.DropAction.MoveAction, drag_md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
target.dropEvent(ev)
QTest.qWait(80)
ok(side_ids() == [c, b, a, d], f"拖放到上半部分 {side_ids()}")
win._on_cat_move(c, a, True)
QTest.qWait(50)
ok(side_ids() == [b, a, c, d], f"复位 {side_ids()}")
win._on_cat_move(d, d, False)  # Alt+↑
QTest.qWait(50)
ok(side_ids() == [b, a, d, c], f"键盘上移 {side_ids()}")

clip = QApplication.clipboard()
small = QImage(64, 48, QImage.Format.Format_RGB32); small.fill(QColor("#aa3355"))
clip.setImage(small)
win.input_edit.setFocus(); QTest.qWait(20)
QTest.keySequence(win.input_edit, QKeySequence(QKeySequence.StandardKey.Paste))
ok(len(win._pending_images) == 1 and win.pending_chip.isVisibleTo(win), "输入框粘贴截图成为待添加图片")
ok(win.save_btn.isEnabled(), "只有图片也能保存")
QTest.keyClick(win.input_edit, Qt.Key.Key_Return)
QTest.qWait(50)
new = [m for m in db.list_memos() if m.content == "图片笔记"]
ok(len(new) == 1 and len(db.list_memo_images(new[0].id)) == 1, "保存后图片挂在新记录上")
ok(win._pending_images == [] and not win.pending_chip.isVisibleTo(win), "保存后清空待添加")

clip.setText("纯文字")
QTest.keySequence(win.input_edit, QKeySequence(QKeySequence.StandardKey.Paste))
ok(win.input_edit.text() == "纯文字" and not win._pending_images, "纯文本照常粘贴")
win.input_edit.clear()

win.select_memo(new[0].id); QTest.qWait(50)
pane = win.detail
ok(pane.memo is not None and pane.memo.id == new[0].id, "详情栏显示该记录")
ok(pane.img_grid.count() == 1 and pane.img_scroll.isVisibleTo(pane), "详情栏显示缩略图")
md = QMimeData(); md.setImageData(small)
pane.title.insertFromMimeData(md)
QTest.qWait(50)
ok(len(db.list_memo_images(new[0].id)) == 2 and pane.img_grid.count() == 2, "详情正文里粘贴图片")
ok("图片" not in pane.title.toPlainText().replace("图片笔记", ""), "粘贴图片不插入文字")
card = win._card(new[0].id)
ok(card is not None and "图片 2" in card.badges_label.text(), "卡片显示图片数")
thumb = pane.img_grid.itemAt(0).widget()
pane._delete_image(thumb.image_id); QTest.qWait(80)
ok(len(db.list_memo_images(new[0].id)) == 1 and pane.img_grid.count() == 1, "详情栏删除图片")
print("ALL OK", flush=True)
os._exit(0)
'''


class ImagesAndReorderTest(unittest.TestCase):
    def test_flow(self) -> None:
        data_dir = tempfile.mkdtemp(prefix="memo-img-")
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
            env["MEMO_APP_DATA_DIR"] = data_dir
            env["QT_QPA_PLATFORM"] = "offscreen"
            proc = subprocess.run(
                [sys.executable, "-c", _SCRIPT], env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            self.assertIn("ALL OK", proc.stdout, proc.stdout + proc.stderr)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
