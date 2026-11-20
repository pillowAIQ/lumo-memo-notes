"""Isolated UI regression checks and reproducible design-review screenshots.

Run: .venv/Scripts/python.exe ui_smoke.py
No user database, tray, global hotkeys or saved settings are touched.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import memo_app  # noqa: F401 - load Windows DLLs before importing Qt
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from memo_app import db, effects
from memo_app.app import CategoryRow, MainWindow, MemoItemWidget, NameDialog
from memo_app.capture import QuickCapture
from memo_app.category_dialog import CategoryPickerDialog
from memo_app.controller import Controller
from memo_app.settings_dialog import SettingsView, SyncSettingsPage
from memo_app.task_dialog import TaskPropsDialog
from memo_app import theme
from memo_app.theme import QSS
from memo_app.widget import TodoWidget

OUT = Path(__file__).parent / "audit_shots" / "redesign"


def settle() -> None:
    QTest.qWait(100)


def shot(widget, name: str) -> None:
    settle()
    assert widget.grab().save(str(OUT / f"{name}.png"))


def card(win: MainWindow, memo_id: int) -> MemoItemWidget:
    for i in range(win.list.count()):
        item = win.list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == memo_id:
            return win.list.itemWidget(item)
    raise AssertionError(f"Memo {memo_id} not visible")


class _SyncStub:
    """设置页需要的最小同步服务替身（无网络/无凭据）。"""

    enabled = False
    base_url = "https://dav.jianguoyun.com/dav/"
    remote_dir = "memo-app"
    conflict_mode = "lww"
    status_text = "未启用"
    directory_cache = {}

    def __init__(self, parent=None):
        pass

    class _Sig:
        def connect(self, *a, **k):
            pass

    status_changed = _Sig()
    conn_tested = _Sig()
    vault_ready = _Sig()
    synced = _Sig()

    def shutdown(self):
        pass

    def vault_id(self):
        return None

    def save_config(self, **kw):
        return True

    def queue_setup(self, *a, **k):
        pass

    def sync_now(self, *a, **k):
        pass

    def test_connection(self, *a, **k):
        pass

    def logout(self):
        pass

    def notify_change(self):
        pass


class SettingsControllerStub:
    sync = _SyncStub()

    def hotkey_display(self):
        return "Ctrl+O"

    def describe_pressed(self):
        return "Ctrl+O"

    def set_hotkey(self, spec):
        return True


def check_manual_sync(ctl: Controller, win: MainWindow) -> None:
    def click_button(role: QMessageBox.ButtonRole) -> None:
        box = QApplication.activeModalWidget()
        for button in box.buttons():
            if box.buttonRole(button) == role:
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                return
        box.reject()
        raise AssertionError(f"Manual sync dialog is missing {role.name}")

    with patch.object(ctl.sync, "sync_now") as sync_now:
        for role, mode in (
            (QMessageBox.ButtonRole.AcceptRole, "lww"),
            (QMessageBox.ButtonRole.ActionRole, "keep"),
            (QMessageBox.ButtonRole.RejectRole, None),
        ):
            QTimer.singleShot(0, lambda role=role: click_button(role))
            ctl.sync_now_interactive(win)
            if mode is None:
                sync_now.assert_not_called()
            else:
                sync_now.assert_called_once_with(mode)
            sync_now.reset_mock()
        settle()
        assert not win.findChildren(QMessageBox)


def check_window_visibility() -> None:
    # Exercise the real controller wiring without a real tray, hotkey or setting.
    with (
        patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True),
        patch.object(QSystemTrayIcon, "show"),
        patch("PySide6.QtCore.QSettings.value", return_value="ctrl+o"),
        patch("pynput.keyboard.Listener"),
        patch.object(Controller, "_check_reminders"),
        patch("memo_app.sync.service.SyncService", _SyncStub),
    ):
        win = MainWindow()
        ctl = Controller(win)
        ctl._remind_timer.stop()
        try:
            win.show()
            settle()
            check_manual_sync(ctl, win)
            assert not ctl.widget.isVisible() and ctl.a_widget.isChecked()
            win.titlebar.min_btn.click()
            settle()
            assert win.isMinimized() and ctl.widget.isVisible()
            first_key = next(iter(ctl.widget._groups))
            ctl.widget._toggle_group(first_key)
            settle()
            assert ctl.widget._groups[first_key]["body"].isVisible()
            ctl.widget.min_btn.click()
            settle()
            assert ctl.widget._minimized and ctl.widget.height() < 70
            assert ctl.widget.list_panel.isHidden()
            assert ctl.widget.min_btn.accessibleName() == "还原桌面挂件"
            shot(ctl.widget, "19_todo_minimized")
            win.reload()
            settle()
            assert ctl.widget._minimized and ctl.widget.height() < 70
            ctl.widget.min_btn.click()
            settle()
            assert not ctl.widget._minimized
            assert ctl.widget._groups[first_key]["body"].isVisible()
            QTest.mouseDClick(ctl.widget, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
            settle()
            assert win.isVisible() and not win.isMinimized()
            assert not ctl.widget.isVisible() and ctl.a_widget.isChecked()

            win.close()
            settle()
            assert not win.isVisible() and ctl.widget.isVisible()
            ctl._on_tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
            settle()
            assert win.isVisible() and not ctl.widget.isVisible()
            win.showMaximized()
            settle()
            assert not ctl.widget.isVisible()
            win.showMinimized()
            settle()
            ctl.widget.hide_btn.click()
            settle()
            assert not ctl.widget.isVisible() and not ctl.a_widget.isChecked()
            ctl.show_main()
            win.showMinimized()
            settle()
            assert not ctl.widget.isVisible()
            ctl.a_widget.setChecked(True)
            settle()
            assert ctl.widget.isVisible()
            ctl.show_main()
            settle()
            ctl.a_widget.setChecked(False)
            ctl.a_widget.setChecked(True)
            settle()
            assert not ctl.widget.isVisible() and ctl.a_widget.isChecked()
            win.showMinimized()
            settle()
            ctl.widget.min_btn.click()
            for _ in range(3):
                ctl.show_main()
                settle()
                assert not ctl.widget.isVisible()
                win.close()
                settle()
                assert ctl.widget.isVisible() and ctl.widget._minimized
                assert ctl.widget.height() < 70
            ctl._prepare_quit()
            ctl._sync_widget_visibility()
            assert not ctl.widget.isVisible()
        finally:
            ctl._prepare_quit()
            win.removeEventFilter(ctl)
            win.tray_available = False
            win.close()
            ctl.widget.close()
            ctl.capture.close()
            QApplication.instance().aboutToQuit.disconnect(ctl._prepare_quit)
            ctl.deleteLater()
            win.deleteLater()
            settle()

        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
            win = MainWindow()
            ctl = Controller(win)
            ctl._remind_timer.stop()
            try:
                win.show()
                settle()
                assert ctl.tray is None and not ctl.widget.isVisible()
                win.showMinimized()
                settle()
                assert ctl.widget.isVisible()
                win.close()
                settle()
                assert not ctl.widget.isVisible()
            finally:
                ctl._prepare_quit()
                win.removeEventFilter(ctl)
                win.close()
                ctl.widget.close()
                ctl.capture.close()
                QApplication.instance().aboutToQuit.disconnect(ctl._prepare_quit)
                ctl.deleteLater()
                win.deleteLater()
                settle()


def check_ui(app: QApplication) -> None:
    db.init_db()
    cats = [db.create_category(n) for n in ("工作与计划", "生活日常", "灵感收集")]
    samples = [
        "不必一次做好所有事，先把最重要的一件做好。",
        "读到一句很喜欢的话：「生活不是等待风暴过去，而是学会在雨中跳舞。」",
        "周末去公园走走，给自己一个不看手机的下午。",
        "关于新项目的想法：先做减法，让最重要的功能更好用。",
        "整理本周的阅读摘记，把喜欢的句子留下来。",
        "清理桌面，准备开始新的一天。",
    ]
    ids = []
    for i, content in enumerate(samples):
        mid = db.add_memo(content)
        db.set_memo_categories(mid, [cats[i % 3]])
        ids.append(mid)
    db.toggle_pin(ids[0])
    db.toggle_done(ids[-1])
    due = int((datetime.now() + timedelta(days=1)).replace(hour=10, minute=0).timestamp())
    task = db.add_memo("和设计团队同步产品方案，准备好讨论要点。", due_ts=due, priority=3)
    db.set_memo_categories(task, [cats[0]])
    discarded = db.add_memo("暂时搁置的旅行计划，下次再出发。")
    db.soft_delete(discarded)

    win = MainWindow()
    win.show()
    settle()
    assert win.memo_count() == 6
    shot(win, "01_main")
    for skin in ("ink", "lavender"):
        theme.apply(skin)
        settle()
        assert win.memo_count() == 6
        shot(win, f"01_main_{skin}")
    # 两种风格：结构差异（页眉 / 分组 / 详情栏显隐）+ 详情栏即时编辑
    for style_key in ("paper", "mica"):
        theme.apply_style(style_key, persist=False)
        settle()
        assert win.memo_count() == 6
        assert win.paper_head.isVisible() == (style_key == "paper")
        assert win.brand_box.isVisible() == (style_key == "mica")
        headers = [win.list.itemWidget(win.list.item(i)) for i in range(win.list.count())
                   if win.list.item(i).data(Qt.ItemDataRole.UserRole) is None]
        assert len(headers) >= 2  # 有期限 / 无期限（或置顶 / 随记）分组
        assert all(not (win.list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable)
                   for i in range(win.list.count()) if win.list.item(i).data(Qt.ItemDataRole.UserRole) is None)
        # 纸笺未选中时安静两栏，云母详情栏常驻（空状态）
        assert win.detail.isVisible() == (style_key == "mica")
        shot(win, f"01_main_style_{style_key}")
        win.select_memo(task)
        settle()
        assert win.detail.isVisible() and win.detail.memo.id == task
        shot(win, f"01_main_style_{style_key}_detail")
        win._close_detail()
        settle()
        assert win.detail.isVisible() == (style_key == "mica") and win.detail.memo is None
    win.select_memo(task)
    settle()
    win.detail.prio_group.button(2).click()
    settle()
    assert db.get_memo(task).priority == 2 and win.detail.memo.priority == 2
    win.detail.prio_group.button(3).click()
    win.detail.title.setFocus()
    win.detail.title.setPlainText("和设计团队同步产品方案，准备好讨论要点！")
    QTest.keyClick(win.detail.title, Qt.Key.Key_Return)
    settle()
    assert db.get_memo(task).content.endswith("要点！") and db.get_memo(task).priority == 3
    assert win.detail.memo.id == task  # 刷新后仍选中同一条
    win.detail.title.setPlainText("和设计团队同步产品方案，准备好讨论要点。")
    QTest.keyClick(win.detail.title, Qt.Key.Key_Return)
    settle()
    # 截止时间防抖期间立刻切走：修改必须写回原条目，不能丢、不能写到新选中的条目
    other = next(mid for _item, mid in win.memo_items() if mid != task)
    other_due = db.get_memo(other).due_ts
    win.detail.due_edit.setDateTime(win.detail.due_edit.dateTime().addSecs(2 * 3600))
    assert win.detail._due_timer.isActive()
    win.select_memo(other)
    settle()
    assert db.get_memo(task).due_ts == due - due % 60 + 2 * 3600  # 详情栏按分钟取整
    assert db.get_memo(other).due_ts == other_due
    db.set_task_props(task, due_ts=due)
    win._close_detail()
    theme.apply("pine", style_key="mica")
    settle()
    win.input_edit.setFocus()
    win.input_edit.setText("让重要的想法，有一个安静的归处。")
    shot(win, "17_composing")
    win.input_edit.clear()
    original_size = win.size()
    win.resize(680, 520)
    settle()
    assert win.width() == 680 and win.height() == 520
    assert win.list.horizontalScrollBar().maximum() == 0
    assert win.list.mapTo(win, QPoint(0, 0)).y() < 260
    assert not win.brand_sub.isVisible() and not win.date_label.isVisible()
    assert not win.detail.isVisible()  # 窄窗：三栏放不下，详情栏让位
    for i in range(win.side_rows.count()):
        row = win.side_rows.itemAt(i).widget()
        if isinstance(row, CategoryRow):
            assert row.mapTo(win, QPoint(0, row.height())).y() <= win.side_footer.mapTo(win, QPoint()).y()
    shot(win, "02_compact")
    win.resize(original_size)
    settle()

    # Real input, keyboard shortcuts, search and state/category intersections.
    win.input_edit.setFocus()
    win.input_edit.setText("测试记录 100%_done <b>不是富文本</b>")
    assert win.save_btn.isEnabled()
    QTest.keyClick(win.input_edit, Qt.Key.Key_Return)
    settle()
    added = db.list_memos(query="100%_done")[0].id
    assert not win.input_edit.text() and not win.save_btn.isEnabled()
    assert card(win, added).content_label.textFormat() == Qt.TextFormat.PlainText
    assert card(win, added)._flash_t > 0 or not effects.enabled()  # new record is briefly highlighted
    QTest.keyClick(win, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    settle()
    assert win.search_edit.hasFocus()
    win.search_edit.setText("100%_done")
    settle()
    assert win.memo_count() == 1
    old_sidebar_row = win.side_rows.itemAt(0).widget()
    win.search_edit.setText("100%")
    settle()
    assert win.side_rows.itemAt(0).widget() is old_sidebar_row
    shot(win, "03_search")
    QTest.keyClick(win.search_edit, Qt.Key.Key_Escape)
    settle()
    assert not win.search_edit.text() and win.memo_count() == 7
    win.filter_buttons.buttons()[2].click()
    settle()
    assert win.memo_count() == 1 and win._memos[0].due_ts is not None
    win.filter_buttons.buttons()[1].click()
    settle()
    assert win.memo_count() == 1 and win._memos[0].is_pinned
    win.filter_buttons.buttons()[0].click()
    win._on_cat_select(cats[0])
    settle()
    assert win.memo_count() == 3
    win.input_edit.setText("这个分类里的新想法")
    win.save_btn.click()
    settle()
    categorized = db.list_memos(query="这个分类里的新想法")[0].id
    assert cats[0] in db.memo_category_map()[categorized]
    shot(win, "04_category")
    win._on_cat_select(0)
    settle()

    item = card(win, added)
    item._start_edit()
    item.edit.setText("不保存这次修改")
    QTest.keyClick(item.edit, Qt.Key.Key_Escape)
    settle()
    assert db.list_memos(query="100%_done")
    item._start_edit()
    item.edit.setText("编辑后的记录")
    QTest.keyClick(item.edit, Qt.Key.Key_Return)
    settle()
    assert db.list_memos(query="编辑后的记录")[0].id == added
    card(win, added).done_btn.click()
    settle()
    assert next(m for m in db.list_memos(trash=True) if m.id == added).is_done
    assert win.toast.isVisible() and win.toast.label.text().startswith("已完成")
    assert win.toast.action.isVisible()
    win._set_trash(True)
    settle()
    card(win, added).restore_btn.click()
    win._on_cat_select(0)
    settle()
    assert not card(win, added).memo.is_done
    card(win, added).del_btn.click()
    settle()
    assert added in [m.id for m in db.list_memos(trash=True)]
    assert win.toast.isVisible() and win.toast.action.text() == "撤销"
    win.toast.action.click()
    settle()
    assert added not in [m.id for m in db.list_memos(trash=True)]
    assert card(win, added)._flash_t > 0 or not effects.enabled()
    fading = card(win, added)
    fading.del_btn.click()
    if effects.enabled():
        assert not fading.isEnabled()  # card is inert while it fades out
        win.delete_memo(added)  # a repeated request during fade-out is ignored
    settle()
    assert added in [m.id for m in db.list_memos(trash=True)]
    QTest.qWait(250)
    assert all(win.list.item(i).data(Qt.ItemDataRole.UserRole) != added for i in range(win.list.count()))
    win.trash_btn.click()
    settle()
    assert win.trash_mode and win.page_title.text() == "回收站"
    assert not win.composer.isVisible() and win.search_edit.isVisible()
    assert "编辑" not in win.footer_hint.text()
    win.search_edit.setFocus()
    QTest.keyClick(win.search_edit, Qt.Key.Key_Escape)
    settle()
    assert win.list.hasFocus()
    shot(win, "05_trash")
    for i in range(win.list.count()):
        if win.list.item(i).data(Qt.ItemDataRole.UserRole) == added:
            win.list.setCurrentRow(i)
            break
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    win._delete_selected()
    assert added in [m.id for m in db.list_memos(trash=True)]
    card(win, added).restore_btn.click()
    settle()
    assert added not in [m.id for m in db.list_memos(trash=True)]
    win._on_cat_select(cats[0])
    settle()
    assert not win.trash_mode and win.composer.isVisible()
    win._on_cat_select(0)
    win.search_edit.setText("不存在的内容")
    settle()
    assert win.stack.currentWidget() is not win.list
    shot(win, "06_no_results")
    win.search_edit.clear()
    settle()
    win.activateWindow()
    settle()
    row = next(win.side_rows.itemAt(i).widget() for i in range(win.side_rows.count())
               if isinstance(win.side_rows.itemAt(i).widget(), CategoryRow)
               and win.side_rows.itemAt(i).widget().cat_id == cats[0])
    row.setFocus()
    settle()
    assert row.del_btn.isVisible()
    QTest.keyClick(row, Qt.Key.Key_Return)
    settle()
    assert win.selected_category == cats[0]
    assert isinstance(QApplication.focusWidget(), CategoryRow)
    win._on_cat_select(0)
    settle()

    # Long text, tags, rich-looking literal text, and narrow layout geometry.
    long_cat = db.create_category("很长的分类名称用于验证窄窗口不会撑破侧栏" * 2)
    long_id = db.add_memo("这是一段长备忘录，用于检验完整换行和高度计算。" * 20)
    db.set_memo_categories(long_id, cats + [long_cat])
    win.reload()
    win.resize(680, 620)
    settle()
    long_card = card(win, long_id)
    assert long_card.content_label.height() >= long_card.content_label.heightForWidth(long_card.content_label.width())
    assert long_card.badges_label.isHidden()
    assert win.list.horizontalScrollBar().maximum() == 0
    for i in range(win.list.count()):
        if win.list.itemWidget(win.list.item(i)) is long_card:
            win.list.scrollToItem(win.list.item(i), win.list.ScrollHint.PositionAtTop)
            break
    shot(win, "07_long_text")
    db.soft_delete(long_id)
    # Enough categories must scroll, not enlarge the minimum window height.
    for i in range(18):
        db.create_category(f"额外分类{i + 1}")
    win.reload()
    win.resize(680, 520)
    settle()
    assert win.height() == 520
    shot(win, "08_many_categories")
    win.resize(original_size)
    settle()

    # 设置对话框会读系统凭据管理器——冒烟测试必须隔离，
    # 避免真实账号信息进入截图/测试环境
    with patch(
        "memo_app.sync.credentials.load_credentials",
        return_value=(None, None),
    ):
        for dialog, name in [
            (TaskPropsDialog(next(m for m in db.list_memos() if m.id == task), win), "09_task_dialog"),
            (CategoryPickerDialog(db.list_categories()[:3], {cats[0]}, win), "10_categories_dialog"),
            (NameDialog("新建分类", "分类名称：", win), "12_new_category"),
        ]:
            dialog.show()
            shot(dialog, name)
            dialog.close()
        # 设置整页：菜单入口直达各分区，Esc 返回笔记
        assert not win.settings_menu.isEmpty()
        win.controller = SettingsControllerStub()
        for page in ("general", "appearance", "hotkeys", "sync", "about"):
            win.open_settings(page)
            settle()
            assert win.body_stack.currentWidget() is win.settings_view
            assert win.settings_view.current_page() == page
            assert not win.search_edit.isVisible()
            shot(win, f"11_settings_{page}")
        win.resize(680, 520)
        for page in ("general", "appearance", "hotkeys", "sync", "about"):
            win.open_settings(page)
            settle()
            area = win.settings_view.scroll
            assert area.widget().minimumSizeHint().width() <= area.viewport().width(), page
        shot(win, "11_settings_compact")
        win.resize(original_size)
        win.open_settings("sync")
        theme.apply("ink")
        settle()
        shot(win, "11_settings_sync_ink")
        win.open_settings("appearance")
        shot(win, "11_settings_appearance_ink")
        paper_card = next(c for c in win.settings_view.pages["appearance"].style_cards.buttons() if c.key == "paper")
        with patch.object(theme, "apply_style", lambda k, persist=True: theme.apply(
                theme.STYLES[k]["palette"], style_key=k)):
            paper_card.click()  # 风格卡：切风格并换上它的推荐配色（隔离：不写 QSettings）
        settle()
        assert theme.style_name() == "paper" and theme.current_name() == "paper"
        shot(win, "11_settings_appearance_paper")
        theme.apply("pine", style_key="mica")
        QTest.keyClick(win.settings_view, Qt.Key.Key_Escape)
        settle()
        assert win.settings_view is None and win.body_stack.currentIndex() == 0
        assert win.search_edit.isVisible()
        win.controller = None

    capture = QuickCapture()
    capture.show()
    shot(capture, "13_quick_capture")
    submitted = []
    capture.submitted.connect(lambda content, category: submitted.append((content, category)))
    capture.edit.setText("/工作与计划 一条快捷记录")
    QTest.keyClick(capture.edit, Qt.Key.Key_Return)
    settle()
    assert submitted == [("一条快捷记录", "工作与计划")]
    capture.show()
    capture.activateWindow()
    capture.edit.setFocus()
    capture.edit.setText("/工作")
    capture._refresh_popup()
    assert capture.popup.count() == 1
    assert capture.popup.isVisible()
    QTest.keyClick(capture.edit, Qt.Key.Key_Tab)
    assert capture.edit.text() == "/工作与计划 "
    capture.popup.close()
    capture.close()

    todo = TodoWidget()
    todo.reload()
    todo.show()
    shot(todo, "14_todo_collapsed")
    win.showMinimized()
    settle()
    assert todo.isVisible()
    assert todo.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    image = todo.grab().toImage()
    scale = image.devicePixelRatio()
    assert 185 <= image.pixelColor(int(8 * scale), int(35 * scale)).alpha() <= 195
    assert image.pixelColor(0, 0).alpha() == 0
    for key in list(todo._groups):
        todo._toggle_group(key)
    settle()
    shot(todo, "15_todo_expanded")
    image = todo.grab().toImage()
    assert 185 <= image.pixelColor(int(8 * scale), int(35 * scale)).alpha() <= 195
    first_group = next(iter(todo._groups.values()))
    QTest.keyClick(first_group["hdr"], Qt.Key.Key_Space)
    assert not first_group["body"].isVisible()
    todo.close()
    win.close()
    check_window_visibility()

    # A separate brand-new database verifies the true first-run empty state.
    with tempfile.TemporaryDirectory(prefix="memo-empty-") as empty:
        os.environ["MEMO_APP_DATA_DIR"] = empty
        empty_win = MainWindow()
        empty_win.show()
        settle()
        assert empty_win.list.count() == 0
        shot(empty_win, "16_empty")
        empty_win.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    # Qt's Windows offscreen plugin does not discover system fonts itself.
    for filename in ("msyh.ttc", "msyhbd.ttc", "georgia.ttf", "SegUIVar.ttf", "segoeui.ttf",
                     "NotoSerifSC-VF.ttf", "STSONG.TTF", "STKAITI.TTF", "simkai.ttf"):
        font = Path("C:/Windows/Fonts") / filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(QSS)
    errors = []
    original_hook = sys.excepthook

    def hook(kind, value, traceback):
        errors.append(str(value))
        original_hook(kind, value, traceback)

    sys.excepthook = hook
    previous_dir = os.environ.get("MEMO_APP_DATA_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="memo-ui-review-") as data:
            os.environ["MEMO_APP_DATA_DIR"] = data
            check_ui(app)
            assert not errors, errors
    finally:
        sys.excepthook = original_hook
        if previous_dir is None:
            os.environ.pop("MEMO_APP_DATA_DIR", None)
        else:
            os.environ["MEMO_APP_DATA_DIR"] = previous_dir
    print(f"PASS: isolated UI interaction/layout checks; screenshots: {OUT}")


if __name__ == "__main__":
    main()
