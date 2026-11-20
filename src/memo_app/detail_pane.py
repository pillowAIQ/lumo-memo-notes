"""主窗口右侧详情栏：直接查看/修改选中备忘录的正文与任务属性。

云母风格常驻（未选中时显示空状态）；纸笺风格选中时展开、可收起。
所有修改即时写库并经 MainWindow 刷新列表；正在输入的正文不会被刷新覆盖。
"""
from __future__ import annotations

from datetime import datetime
from html import escape

from PySide6.QtCore import QDateTime, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QTextOption
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateTimeEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import db, theme
from .db import Memo
from .icons import ui_icon

ADVANCE_CHOICES = (
    ("到期时提醒", 0),
    ("提前 5 分钟", 5),
    ("提前 15 分钟", 15),
    ("提前 30 分钟", 30),
    ("提前 1 小时", 60),
    ("提前 1 天", 1440),
)


def _stamp(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    today = datetime.now().date()
    if dt.date() == today:
        return f"今天 {dt:%H:%M}"
    if dt.year == today.year:
        return f"{dt.month}月{dt.day}日 {dt:%H:%M}"
    return f"{dt.year}年{dt.month}月{dt.day}日"


class _TitleEdit(QPlainTextEdit):
    """正文编辑：随内容增高；Enter 保存，Shift+Enter 换行，Esc 放弃。"""

    commit = Signal()
    revert = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("detailTitle")
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setTabChangesFocus(True)
        self.setAccessibleName("备忘录正文")
        self.document().contentsChanged.connect(self._fit)

    def _fit(self) -> None:
        doc_h = self.document().size().height() * self.fontMetrics().lineSpacing()
        margins = self.contentsMargins().top() + self.contentsMargins().bottom()
        h = int(min(220, max(self.fontMetrics().lineSpacing() + 10, doc_h + margins + 10)))
        if h != self.height():
            self.setFixedHeight(h)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.commit.emit()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.revert.emit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        super().focusOutEvent(e)
        # 推迟一轮：若失焦是因为点了另一条，选中切换会先把正文写回旧条目，
        # 这里再执行时正文已换成新条目内容，自然成为空操作
        QTimer.singleShot(0, self, self.commit.emit)


class DetailPane(QWidget):
    """选中备忘录的详情与属性编辑。"""

    close_requested = Signal()

    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.memo: Memo | None = None
        self.setObjectName("detailPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(340)
        self._loading = False
        self._force_text = False
        # 日期时间控件逐位微调会连发 dateTimeChanged：合并后再写库
        self._due_timer = QTimer(self)
        self._due_timer.setSingleShot(True)
        self._due_timer.setInterval(600)
        self._due_timer.timeout.connect(self._save_due)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        # ---- 空状态 ----
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(2)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_icon)
        el.addSpacing(10)
        tip = QLabel("选中一条笔记\n在这里修改截止、提醒、重复与优先级")
        tip.setObjectName("emptyLabel")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(tip)
        el.addStretch(3)
        self.stack.addWidget(empty)

        # ---- 详情 ----
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 22, 18, 18)
        lay.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.crumb = QLabel()
        self.crumb.setObjectName("detailCrumb")
        self.crumb.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self.crumb, 1)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("sectionAdd")
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.setIconSize(QSize(13, 13))
        self.close_btn.setToolTip("收起详情")
        self.close_btn.setAccessibleName("收起详情")
        self.close_btn.clicked.connect(self.close_requested.emit)
        top.addWidget(self.close_btn)
        lay.addLayout(top)
        lay.addSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        self.done_btn = QPushButton()
        self.done_btn.setObjectName("doneBtn")
        self.done_btn.setFixedSize(22, 22)
        self.done_btn.setToolTip("标记完成")
        self.done_btn.setAccessibleName("标记完成")
        self.done_btn.clicked.connect(self._on_done)
        title_row.addWidget(self.done_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.title = _TitleEdit()
        self.title.commit.connect(self._save_text)
        self.title.revert.connect(self._revert_text)
        title_row.addWidget(self.title, 1)
        lay.addLayout(title_row)
        lay.addSpacing(18)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnMinimumWidth(0, 64)
        self._field_icons: list[tuple[QLabel, str]] = []

        def label(icon: str, text: str, row: int) -> None:
            w = QWidget()
            wl = QHBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(8)
            ic = QLabel()
            self._field_icons.append((ic, icon))
            wl.addWidget(ic)
            t = QLabel(text)
            t.setObjectName("fieldLabel")
            wl.addWidget(t)
            wl.addStretch(1)
            grid.addWidget(w, row, 0)

        # 截止：未设置时显示"添加"按钮，设置后显示日期控件 + 清除
        label("calendar", "截止", 0)
        due_host = QWidget()
        dl = QHBoxLayout(due_host)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        self.due_add = QPushButton("添加截止时间")
        self.due_add.setObjectName("chipGhost")
        self.due_add.clicked.connect(self._add_due)
        dl.addWidget(self.due_add)
        self.due_edit = QDateTimeEdit()
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("M月d日 HH:mm")
        self.due_edit.setAccessibleName("截止时间")
        self.due_edit.dateTimeChanged.connect(self._on_due_changed)
        dl.addWidget(self.due_edit, 1)
        self.due_clear = QToolButton()
        self.due_clear.setObjectName("sectionAdd")
        self.due_clear.setFixedSize(26, 26)
        self.due_clear.setIconSize(QSize(12, 12))
        self.due_clear.setToolTip("清除截止时间")
        self.due_clear.setAccessibleName("清除截止时间")
        self.due_clear.clicked.connect(self._clear_due)
        dl.addWidget(self.due_clear)
        grid.addWidget(due_host, 0, 1)

        label("bell", "提醒", 1)
        self.advance = QComboBox()
        self.advance.setAccessibleName("提前提醒")
        for text, mins in ADVANCE_CHOICES:
            self.advance.addItem(text, mins)
        self.advance.activated.connect(self._save_advance)
        grid.addWidget(self.advance, 1, 1)

        label("repeat", "重复", 2)
        self.recur = QComboBox()
        self.recur.setAccessibleName("重复")
        self.recur.addItem("不重复", None)
        for key, text in db.RECUR_LABELS.items():
            self.recur.addItem(text, key)
        self.recur.activated.connect(self._save_recur)
        grid.addWidget(self.recur, 2, 1)

        label("flag", "优先级", 3)
        prio_host = QWidget()
        prio_host.setObjectName("filterGroup")
        prio_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pl = QHBoxLayout(prio_host)
        pl.setContentsMargins(3, 3, 3, 3)
        pl.setSpacing(2)
        self.prio_group = QButtonGroup(self)
        for p in (0, 1, 2, 3):
            b = QPushButton(db.PRIORITY_LABELS[p])
            b.setObjectName("filterBtn")
            b.setCheckable(True)
            b.setAccessibleName(f"优先级{db.PRIORITY_LABELS[p]}")
            self.prio_group.addButton(b, p)
            pl.addWidget(b, 1)
        self.prio_group.idClicked.connect(self._save_priority)
        grid.addWidget(prio_host, 3, 1)

        label("folder", "分类", 4)
        cat_host = QWidget()
        cl = QHBoxLayout(cat_host)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.cats = QLabel()
        self.cats.setObjectName("detailCats")
        self.cats.setTextFormat(Qt.TextFormat.RichText)
        self.cats.setWordWrap(True)
        self.cats.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cl.addWidget(self.cats, 1)
        self.cat_btn = QPushButton("选择…")
        self.cat_btn.setObjectName("chipGhost")
        self.cat_btn.clicked.connect(lambda: self.memo and self.win.assign_categories(self.memo.id))
        cl.addWidget(self.cat_btn)
        grid.addWidget(cat_host, 4, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addStretch(1)

        line = QWidget()
        line.setObjectName("rowLine")
        line.setFixedHeight(1)
        lay.addWidget(line)
        lay.addSpacing(12)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.stamp = QLabel()
        self.stamp.setObjectName("statusLabel")
        self.stamp.setWordWrap(True)
        foot.addWidget(self.stamp, 1)
        self.pin_btn = QPushButton("置顶")
        self.pin_btn.setObjectName("chipGhost")
        self.pin_btn.setCheckable(True)
        self.pin_btn.clicked.connect(lambda: self.memo and self.win.toggle_pin(self.memo.id))
        foot.addWidget(self.pin_btn)
        self.del_btn = QPushButton("删除")
        self.del_btn.setObjectName("chipDanger")
        self.del_btn.clicked.connect(lambda: self.memo and self.win.delete_memo(self.memo.id))
        foot.addWidget(self.del_btn)
        lay.addLayout(foot)
        self.stack.addWidget(page)
        self.page = page

        for b in (self.due_add, self.due_clear, self.cat_btn, self.pin_btn, self.del_btn,
                  self.done_btn, self.close_btn, *self.prio_group.buttons()):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retheme()
        self.show_memo(None)

    # ---------- 外观 ----------

    def retheme(self) -> None:
        muted = theme.c("muted")
        for ic, name in self._field_icons:
            ic.setPixmap(ui_icon(name, muted, 15).pixmap(15, 15))
        self.close_btn.setIcon(ui_icon("close", muted, 13))
        self.due_clear.setIcon(ui_icon("close", muted, 12))
        self.empty_icon.setPixmap(ui_icon("note", theme.c("faint"), 34).pixmap(34, 34))
        self.close_btn.setVisible(theme.is_paper())
        if self.memo is not None:
            self._fill(self.memo, keep_text=self.title.hasFocus())

    # ---------- 数据 ----------

    def show_memo(self, memo: Memo | None) -> None:
        """切换到某条备忘录（None = 空状态）。同一条再次传入 = 刷新字段。"""
        same = memo is not None and self.memo is not None and memo.id == self.memo.id
        # 正文有未提交的修改（聚焦中，或刚失焦、推迟的提交还没执行）：刷新时保留
        dirty = same and self.title.toPlainText().strip() != self.memo.content
        if not same and self.memo is not None:
            self._flush_pending(self.memo)
        self.memo = memo
        if memo is None:
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentWidget(self.page)
        self._fill(memo, keep_text=same and (self.title.hasFocus() or dirty))

    def _fill(self, m: Memo, keep_text: bool) -> None:
        self._loading = True
        try:
            if not keep_text or self._force_text:
                self._force_text = False
                if self.title.toPlainText() != m.content:
                    self.title.setPlainText(m.content)
            done = m.is_done
            self.done_btn.setProperty("done", "true" if done else "false")
            self.done_btn.setIcon(ui_icon("check", theme.c("on_accent"), 13) if done else QIcon())
            self.done_btn.setToolTip("取消完成" if done else "标记完成")
            self.done_btn.style().unpolish(self.done_btn)
            self.done_btn.style().polish(self.done_btn)

            has_due = m.due_ts is not None
            self.due_add.setVisible(not has_due)
            self.due_edit.setVisible(has_due)
            self.due_clear.setVisible(has_due)
            if has_due and not self._due_timer.isActive():
                # 今年的日期省略年份，栏宽有限；跨年才显示
                same_year = datetime.fromtimestamp(m.due_ts).year == datetime.now().year
                self.due_edit.setDisplayFormat("M月d日 HH:mm" if same_year else "yyyy年M月d日 HH:mm")
                self.due_edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(m.due_ts)))
            overdue = has_due and m.due_ts < datetime.now().timestamp()
            self.due_edit.setProperty("overdue", "true" if overdue else "false")
            self.due_edit.style().unpolish(self.due_edit)
            self.due_edit.style().polish(self.due_edit)

            idx = self.advance.findData(m.remind_advance)
            if idx < 0:
                # 自然语言写入的自定义提前量：补一项以便正确回显
                self.advance.addItem(f"提前 {m.remind_advance} 分钟", m.remind_advance)
                idx = self.advance.count() - 1
            self.advance.setCurrentIndex(idx)
            self.advance.setEnabled(has_due)
            ridx = self.recur.findData(m.recur)
            self.recur.setCurrentIndex(ridx if ridx >= 0 else 0)
            self.recur.setEnabled(has_due)
            self.advance.setToolTip("" if has_due else "先设置截止时间")
            self.recur.setToolTip("" if has_due else "先设置截止时间")
            button = self.prio_group.button(max(0, min(3, m.priority)))
            if button is not None:
                button.setChecked(True)

            names = self._cat_html(m.id)
            self.cats.setText(names or f'<span style="color:{theme.c("faint")}">未分类</span>')
            self.crumb.setText(names or f'<span style="color:{theme.c("muted")}">未分类</span>')
            self.pin_btn.setChecked(m.is_pinned)
            self.pin_btn.setText("已置顶" if m.is_pinned else "置顶")
            created, updated = _stamp(m.created_at), _stamp(m.updated_at)
            self.stamp.setText(f"创建于 {created}" + (f" · 修改于 {updated}" if updated != created else ""))
        finally:
            self._loading = False

    def _cat_html(self, memo_id: int) -> str:
        ids = sorted(self.win._memo_cats.get(memo_id, set()))
        parts = []
        for cid in ids:
            name = self.win._cat_names.get(cid)
            if name:
                safe = escape(name)
                parts.append(
                    f'<span style="color:{theme.cat_color(cid)}">●</span>'
                    f'<span style="color:{theme.c("text_2")}">&nbsp;{safe}</span>'
                )
        return "&nbsp;&nbsp;&nbsp;".join(parts)

    # ---------- 保存 ----------

    def _flush_pending(self, old: Memo) -> None:
        """切走前把旧条目未保存的正文 / 防抖中的截止时间写回旧条目。

        常在列表选中变化信号里被调用：写库会触发 reload 重建列表，
        不能在信号处理中同步进行——推迟到下一轮事件循环，并显式绑定旧条目 id。
        """
        text = self.title.toPlainText().strip()
        new_text = text if text and text != old.content else None
        new_due = None
        if self._due_timer.isActive():
            self._due_timer.stop()
            if old.due_ts is not None:  # 不看可见性：详情栏整体隐藏时子控件也"不可见"
                ts = int(self.due_edit.dateTime().toSecsSinceEpoch())
                ts -= ts % 60
                new_due = ts if ts != old.due_ts else None
        if new_text is None and new_due is None:
            return
        memo_id, win = old.id, self.win

        def write() -> None:
            if db.get_memo(memo_id) is None:  # 推迟期间已被彻底删除：不再写（否则同步会复活它）
                return
            if new_text is not None:
                win.edit_memo(memo_id, new_text)
            if new_due is not None:
                win.set_task_props(memo_id, due_ts=new_due)

        QTimer.singleShot(0, self, write)

    def _save_text(self) -> None:
        if self._loading or self.memo is None:
            return
        text = self.title.toPlainText().strip()
        if text and text != self.memo.content:
            # 自然语言解析可能改写正文（"明天开会"→"开会"+截止）：先置位，让 edit_memo 内部的
            # 刷新立即以库为准；保存失败则复位，保留编辑框里的文字
            self._force_text = True
            if not self.win.edit_memo(self.memo.id, text):
                self._force_text = False

    def _revert_text(self) -> None:
        if self.memo is not None:
            self.title.setPlainText(self.memo.content)
        self.win.input_edit.setFocus()

    def _on_done(self) -> None:
        if self.memo is not None:
            self.win.toggle_done(self.memo.id)

    def _set_props(self, **props) -> None:
        if self._loading or self.memo is None:
            return
        self.win.set_task_props(self.memo.id, **props)

    def _add_due(self) -> None:
        # 默认下一个整点，用户再按需微调
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        self._set_props(due_ts=int(now.timestamp()) + 3600)

    def _clear_due(self) -> None:
        self._due_timer.stop()
        self._set_props(due_ts=None)

    def _on_due_changed(self, _dt) -> None:
        if not self._loading:
            self._due_timer.start()

    def _save_due(self) -> None:
        if self.memo is None or self.memo.due_ts is None:
            return
        ts = int(self.due_edit.dateTime().toSecsSinceEpoch())
        ts -= ts % 60
        if ts != self.memo.due_ts:
            self._set_props(due_ts=ts)

    def _save_advance(self, _index: int) -> None:
        self._set_props(remind_advance=int(self.advance.currentData()))

    def _save_recur(self, _index: int) -> None:
        self._set_props(recur=self.recur.currentData())

    def _save_priority(self, prio: int) -> None:
        if self.memo is not None and prio != self.memo.priority:
            self._set_props(priority=prio)
