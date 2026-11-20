"""桌面待办挂件"展开全部"回归测试（子进程 offscreen 隔离）。

覆盖需求点：
- 分组底部"… 还有 N 条"是可点击、可键盘（Tab 聚焦 + 空格/回车）触发的按钮，
  展开该组全部剩余备忘录，并提供"收起"回到前 GROUP_MAX_ROWS 条；
- show-all 状态独立于分组折叠状态，跨 reload / 折叠重开 / 挂件最小化还原保留；
- 完成/删除/同步触发 reload 后计数与按钮文案仍正确，不丢备忘录；
- 内容变高时列表进入有界滚动区：纵向滚轮/滚动条可用、无横向溢出，
  面板在展开、reload、拖到屏幕边缘后仍保持在可用屏幕几何内。

全部在子进程内用临时 MEMO_APP_DATA_DIR + offscreen 平台运行，
不碰真实数据库/设置/托盘/热键/云同步。

运行：.venv/Scripts/python.exe -m unittest discover -s tests -p test_widget_expansion.py
     .venv/Scripts/python.exe tests/test_widget_expansion.py   # 同上（父进程模式）
     WIDGET_EXPANSION_CHILD=1 .venv/Scripts/python.exe tests/test_widget_expansion.py  # 仅跑子进程检查
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

_CHILD_ENV = "WIDGET_EXPANSION_CHILD"


def _rmtree_retry(path: str, attempts: int = 30) -> None:
    """Windows 上子进程句柄释放有延迟，清理临时目录需重试。"""
    for _ in range(attempts):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return
        except OSError:
            time.sleep(0.2)
    shutil.rmtree(path, ignore_errors=True)


def _child_main() -> int:
    """offscreen 子进程主体：真实 TodoWidget + 真实 SQLite（临时目录）。"""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(SRC))
    import memo_app  # noqa: F401 - Windows DLL 预加载须先于 PySide6
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QGuiApplication, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from memo_app import db
    from memo_app.widget import GROUP_MAX_ROWS, TodoWidget

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))
        suffix = f"  ({detail})" if detail else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}", flush=True)

    def settle(ms: int = 140) -> None:
        QTest.qWait(ms)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        db.init_db()
        cat = db.create_category("展开测试")
        total = GROUP_MAX_ROWS + 4  # 10 条：6 + 4
        ids = []
        for i in range(1, total + 1):
            mid = db.add_memo(f"展开条目{i:02d}")
            db.set_memo_categories(mid, [cat])
            ids.append(mid)

        w = TodoWidget()
        w._memo_provider = db.list_memos
        w.done_toggled.connect(lambda mid: (db.toggle_done(mid), w.reload()))
        w.reload()
        w.show()
        settle()

        key = str(cat)

        def rows_of(body) -> list:
            out = []
            lay = body.layout()
            for i in range(lay.count()):
                wd = lay.itemAt(i).widget()
                if wd is not None and wd.objectName() == "widgetRow":
                    out.append(wd)
            return out

        def more_btn(body):
            lay = body.layout()
            for i in range(lay.count()):
                wd = lay.itemAt(i).widget()
                if isinstance(wd, QPushButton) and wd.objectName() == "widgetMore":
                    return wd
            return None

        def row_texts(body) -> list[str]:
            texts = []
            for r in rows_of(body):
                lab = r.findChild(QLabel, "widgetItem")
                texts.append(lab.text() if lab is not None else "")
            return texts

        def inside_geo(widget) -> bool:
            geo = QGuiApplication.primaryScreen().availableGeometry()
            r = widget.geometry()
            return (r.left() >= geo.left() and r.right() <= geo.right()
                    and r.top() >= geo.top() and r.bottom() <= geo.bottom())

        # ---- 1. 折叠态：前 6 条 + 可点击的"还有"按钮 ----
        g = w._groups[key]
        QTest.mouseClick(g["hdr"], Qt.MouseButton.LeftButton,
                         pos=g["hdr"].rect().center())
        settle()
        btn = more_btn(g["body"])
        check("折叠态显示前6条+还有按钮",
              len(rows_of(g["body"])) == GROUP_MAX_ROWS
              and btn is not None and "还有" in btn.text(),
              btn.text() if btn is not None else "无按钮")
        check("按钮可键盘聚焦", btn is not None and btn.focusPolicy() == Qt.FocusPolicy.StrongFocus)

        # ---- 2. 空格键展开全部 ----
        btn.setFocus()
        QTest.keyClick(btn, Qt.Key.Key_Space)
        settle()
        g = w._groups[key]
        texts = row_texts(g["body"])
        check("空格展开全部条目", key in w._show_all and len(rows_of(g["body"])) == total,
              f"rows={len(rows_of(g['body']))}")
        check("末位记录可见", bool(texts) and texts[-1] == "展开条目01", texts[-1] if texts else "")
        btn = more_btn(g["body"])
        check("出现收起按钮", btn is not None and btn.text() == "收起")

        # ---- 3. 对末位记录点完成：不丢备忘录、计数仍正确 ----
        last_row = rows_of(g["body"])[-1]
        done_btn = last_row.findChild(QPushButton)
        done_btn.click()
        settle(500)  # 完成反馈动画(NORMAL≈180ms) + reload
        done_memo = next((m for m in db.list_memos(trash=True) if m.id == ids[0]), None)
        check("末位记录可标记完成", done_memo is not None and done_memo.is_done)
        g = w._groups[key]
        check("完成后仍展开全部且计数正确",
              key in w._show_all and len(g["memos"]) == total - 1
              and len(rows_of(g["body"])) == total - 1,
              f"memos={len(g['memos'])} rows={len(rows_of(g['body']))}")
        check("收起按钮仍在", more_btn(g["body"]) is not None and more_btn(g["body"]).text() == "收起")

        # ---- 4. 收起回到 6 条 ----
        more_btn(g["body"]).click()
        settle()
        g = w._groups[key]
        btn = more_btn(g["body"])
        check("收起回到6条",
              key not in w._show_all and len(rows_of(g["body"])) == GROUP_MAX_ROWS
              and btn is not None and "还有 3 条" in btn.text(),
              btn.text() if btn is not None else "无按钮")

        # ---- 5. reload 保留 show-all ----
        more_btn(g["body"]).click()
        settle()
        w.reload()
        settle(60)
        g = w._groups[key]
        check("reload 保留展开全部", key in w._show_all and len(rows_of(g["body"])) == total - 1)

        # ---- 6. 分组折叠/重开保留 show-all ----
        QTest.mouseClick(g["hdr"], Qt.MouseButton.LeftButton,
                         pos=g["hdr"].rect().center())
        settle()
        check("折叠隐藏正文", not g["body"].isVisible())
        QTest.mouseClick(g["hdr"], Qt.MouseButton.LeftButton,
                         pos=g["hdr"].rect().center())
        settle()
        check("重开仍全部展开", g["body"].isVisible() and len(rows_of(g["body"])) == total - 1)

        # ---- 7. 挂件最小化/还原保留 show-all ----
        w.min_btn.click()
        settle(60)
        check("最小化隐藏列表", w.list_scroll.isHidden() and w.list_panel.isHidden())
        w.min_btn.click()
        settle(120)
        g = w._groups[key]
        check("还原后仍全部展开", key in w._show_all and len(rows_of(g["body"])) == total - 1)

        # ---- 8. 高内容：有界滚动 + 滚轮可达末行 + 面板在屏幕内 ----
        for i in range(60):
            mid = db.add_memo(f"滚动样本{i + 1:02d}")
            db.set_memo_categories(mid, [cat])
        w.reload()
        settle(60)
        g = w._groups[key]
        scroll = w.list_scroll
        sb = scroll.verticalScrollBar()
        check("高内容全部行", key in w._show_all and len(rows_of(g["body"])) == len(g["memos"]),
              f"rows={len(rows_of(g['body']))} memos={len(g['memos'])}")
        check("出现纵向滚动", sb.maximum() > 0, f"max={sb.maximum()}")
        check("无横向滚动条", scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        check("面板高度有界", w.height() <= QGuiApplication.primaryScreen().availableGeometry().height(),
              f"h={w.height()}")
        check("面板在屏幕可用区内", inside_geo(w), f"{w.geometry()}")

        before = sb.value()
        wheel = QWheelEvent(
            QPointF(20, 20), scroll.viewport().mapToGlobal(QPoint(20, 20)),
            QPoint(0, -240), QPoint(0, -240),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        # 滚轮事件投递到 viewport（真实事件中落在条目上的滚轮会沿父链冒泡到这里）
        QApplication.sendEvent(scroll.viewport(), wheel)
        settle(40)
        check("滚轮可向下滚动", sb.value() > before, f"{before} -> {sb.value()}")

        sb.setValue(sb.maximum())
        settle(60)
        last = rows_of(g["body"])[-1]
        vp = scroll.viewport()
        pos = last.mapTo(vp, QPoint(0, 0))
        check("滚到底部末行可见",
              0 <= pos.y() and pos.y() + last.height() <= vp.height() + 2,
              f"y={pos.y()} row_h={last.height()} vp_h={vp.height()}")

        # ---- 9. 允许一半藏到屏幕外：reload 后不弹回 ----
        geo = QGuiApplication.primaryScreen().availableGeometry()
        target = (geo.right() - w.width() // 2, geo.bottom() - w.height() + 50)
        w.move(*target)
        w._clamp_to_screen()
        w.reload()
        settle(60)
        check("藏一半到屏幕外不弹回", (w.x(), w.y()) == target, f"{w.pos()} vs {target}")

        # ---- 10. 整个拖出屏幕：拉回到至少露出一截，且顶部不越出屏幕 ----
        w.move(geo.right() + 400, geo.top() - 200)
        w._clamp_to_screen()
        visible = w.geometry().intersected(geo)
        check("拖出屏幕后仍露出一截", visible.width() >= 60 and w.y() >= geo.top(),
              f"{w.geometry()} vs {geo}")

        w.close()
        settle(40)
    finally:
        app.processEvents()

    failed = [name for name, ok, _ in results if not ok]
    print("-" * 50, flush=True)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {', '.join(failed)}", flush=True)
        return 1
    print(f"ALL CHECKS PASSED ({len(results)})", flush=True)
    return 0


class WidgetExpansionTest(unittest.TestCase):
    def test_group_show_all_lifecycle(self) -> None:
        """子进程 offscreen：分组展开全部/收起/状态保留/滚动与屏幕边界。"""
        data_dir = tempfile.mkdtemp(prefix="memo-widget-expand-")
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
            env["MEMO_APP_DATA_DIR"] = data_dir
            env[_CHILD_ENV] = "1"
            env.pop("QT_QPA_PLATFORM", None)  # 由子进程自行设为 offscreen
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                env=env,
                capture_output=True,
                text=True,
                timeout=240,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("ALL CHECKS PASSED", proc.stdout)
        finally:
            _rmtree_retry(data_dir)


if __name__ == "__main__":
    if os.environ.get(_CHILD_ENV):
        sys.exit(_child_main())
    unittest.main()
