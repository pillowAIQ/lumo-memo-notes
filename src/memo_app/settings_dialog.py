"""设置对话框：呼出快捷键 + 坚果云同步配置。"""
from __future__ import annotations

import uuid as uuidlib

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.ctl = controller
        self._recording = False
        # 关闭即销毁：避免重复打开时 signal receiver 泄漏
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(18)
        heading = QLabel("随时，记下你的想法")
        heading.setObjectName("dialogTitle")
        hint = QLabel("设置一个顺手的快捷键，在任何应用中呼出快捷记录。")
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        lay.addWidget(heading)
        lay.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("呼出快捷键"))
        self.hotkey_edit = QLineEdit(controller.hotkey_display())
        self.hotkey_edit.setPlaceholderText("点击录制，或手动输入如 Ctrl+Shift+K")
        row.addWidget(self.hotkey_edit, 1)
        self.record_btn = QPushButton("点击录制")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setCheckable(True)
        row.addWidget(self.record_btn)
        lay.addLayout(row)

        self.tip = QLabel(
            "点击录制后按下组合键（需含 Ctrl/Shift/Alt/Win + 一个键）；"
            "也可直接在输入框手动输入，如 Ctrl+P"
        )
        self.tip.setObjectName("dialogHint")
        self.tip.setWordWrap(True)
        lay.addWidget(self.tip)

        self._build_sync_section(lay)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        lay.addWidget(buttons)

        self.record_btn.toggled.connect(self._on_record_toggled)
        # 手动输入时自动退出录制，避免 _poll 覆盖正在输入的内容
        self.hotkey_edit.textEdited.connect(self._on_manual_edit)
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._poll)

    def _on_record_toggled(self, on: bool) -> None:
        self._recording = on
        self.record_btn.setText("录制中… 再点取消" if on else "点击录制")
        if on:
            self.hotkey_edit.clear()
            self.hotkey_edit.setPlaceholderText("按下快捷键…")
            self._timer.start()
        else:
            self._timer.stop()
            self.hotkey_edit.setPlaceholderText("点击录制，或手动输入如 Ctrl+Shift+K")

    def _on_manual_edit(self, _text: str) -> None:
        if self._recording:
            self.record_btn.setChecked(False)

    def _poll(self) -> None:
        if not self._recording:
            return
        try:
            desc = self.ctl.describe_pressed()
        except Exception:
            return
        if desc:
            self.hotkey_edit.setText(desc)
            self.record_btn.setChecked(False)
            self._on_record_toggled(False)

    # ---------- 同步设置（分步引导 + 实时反馈） ----------

    def _build_sync_section(self, lay) -> None:
        svc = self.ctl.sync
        sep = QLabel("同步（坚果云 WebDAV + 端到端加密）")
        sep.setObjectName("dialogTitle")
        lay.addWidget(sep)

        self.sync_enabled = QCheckBox("启用多设备同步")
        self.sync_enabled.setChecked(svc.enabled)
        lay.addWidget(self.sync_enabled)

        def _row(label, widget):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(QLabel(label))
            row.addWidget(widget, 1)
            lay.addLayout(row)
            return row

        def _step(text: str) -> None:
            lb = QLabel(text)
            lb.setObjectName("dialogTitle")
            lay.addWidget(lb)

        # ── 第 1 步：连接坚果云 ──────────────────────────
        _step("第 1 步 · 连接坚果云账号")

        self.sync_url = QLineEdit(svc.base_url)
        self.sync_url.setPlaceholderText("https://dav.jianguoyun.com/dav/")
        _row("服务器地址", self.sync_url)

        self.sync_account = QLineEdit()
        self.sync_account.setPlaceholderText("坚果云账号（邮箱）")
        _row("账号", self.sync_account)

        pw_row = QHBoxLayout()
        pw_row.setSpacing(8)
        pw_row.addWidget(QLabel("应用密码"))
        self.sync_app_pw = QLineEdit()
        self.sync_app_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.sync_app_pw.setPlaceholderText(
            "第三方应用密码（坚果云 → 账户信息 → 安全选项 生成）"
        )
        pw_row.addWidget(self.sync_app_pw, 1)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._on_test_conn)
        pw_row.addWidget(self.test_btn)
        lay.addLayout(pw_row)

        self.conn_result = QLabel("")
        self.conn_result.setWordWrap(True)
        lay.addWidget(self.conn_result)

        # ── 第 2 步：创建或加入同步库 ─────────────────────
        _step("第 2 步 · 创建或加入同步库")

        self.vault_pw = QLineEdit()
        self.vault_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.vault_pw.setPlaceholderText(
            "同步库密码（加密密钥，两台设备必须相同，务必牢记）"
        )
        _row("同步库密码", self.vault_pw)

        vault_row = QHBoxLayout()
        vault_row.setSpacing(8)
        self.vault_new = QRadioButton("新建同步库")
        self.vault_join = QRadioButton("加入已有同步库")
        self.vault_new.setChecked(True)
        vault_row.addWidget(self.vault_new)
        vault_row.addWidget(self.vault_join)
        vault_row.addStretch(1)
        lay.addLayout(vault_row)

        self.vault_id_edit = QLineEdit()
        self.vault_id_edit.setPlaceholderText(
            "同步库 ID（另一台设备的设置里可复制给你）"
        )
        self.vault_id_edit.setEnabled(False)
        _row("同步库 ID", self.vault_id_edit)
        self.vault_join.toggled.connect(
            lambda on: self.vault_id_edit.setEnabled(on)
        )
        self.vault_new.toggled.connect(lambda _on: self._sync_setup_label())
        self.vault_join.toggled.connect(lambda _on: self._sync_setup_label())

        setup_row = QHBoxLayout()
        setup_row.addStretch(1)
        self.setup_btn = QPushButton("创建同步库")
        self.setup_btn.clicked.connect(self._on_setup)
        setup_row.addWidget(self.setup_btn)
        lay.addLayout(setup_row)

        # 库就绪信息（vault ID + 复制 + 存放位置说明）
        self.vault_box = QWidget()
        vb = QVBoxLayout(self.vault_box)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(4)
        self.vault_info = QLabel("")
        self.vault_info.setWordWrap(True)
        vb.addWidget(self.vault_info)
        copy_row = QHBoxLayout()
        copy_row.addStretch(1)
        self.copy_vid_btn = QPushButton("复制库 ID")
        self.copy_vid_btn.clicked.connect(self._copy_vault_id)
        copy_row.addWidget(self.copy_vid_btn)
        vb.addLayout(copy_row)
        self.vault_box.hide()
        lay.addWidget(self.vault_box)
        self._vault_id = ""
        self._setup_pending = False  # 建库/加入 job 进行中
        # 异步任务期间锁定的输入控件
        self._sync_inputs = [
            self.sync_enabled, self.sync_url, self.sync_account,
            self.sync_app_pw, self.test_btn, self.vault_pw,
            self.vault_new, self.vault_join, self.vault_id_edit,
            self.copy_vid_btn,
        ]

        # ── 第 3 步：状态 ────────────────────────────────
        _step("第 3 步 · 同步状态")
        self.sync_status = QLabel(svc.status_text)
        self.sync_status.setObjectName("dialogHint")
        self.sync_status.setWordWrap(True)
        lay.addWidget(self.sync_status)
        svc.status_changed.connect(self._on_status_text)
        svc.conn_tested.connect(self._on_conn_tested)
        svc.vault_ready.connect(self._on_vault_ready)

        cur_vid = svc.vault_id()
        if cur_vid:
            self._show_vault_ready(cur_vid, announce=False)

        try:
            from .sync import credentials
            acc, _pw = credentials.load_credentials() or (None, None)
        except Exception:
            acc = None
        if acc:
            self.sync_account.setText(acc)

        hint = QLabel(
            "依次完成第 1、2 步即可开始同步；之后全自动，"
            "也可在主界面标题栏「同步」按钮手动触发。"
            "同步库密码遗忘则无法找回加密数据。"
        )
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

    # ---------- 同步引导动作 ----------

    def _sync_setup_label(self) -> None:
        if not self._vault_id:  # 已绑定库时保持"已绑定"文案
            self.setup_btn.setText(
                "加入同步库"
                if self.vault_join.isChecked()
                else "创建同步库"
            )

    def _set_sync_pending(self, on: bool) -> None:
        """建库/加入进行中锁定同步输入，防止表单与实际 job 不一致。"""
        self._setup_pending = on
        for w in self._sync_inputs:
            w.setEnabled(not on)
        # 解锁后恢复单选状态对应的库 ID 可用性
        self.vault_id_edit.setEnabled(
            (not on) and self.vault_join.isChecked()
        )
        if on:
            self.setup_btn.setEnabled(False)
            self.setup_btn.setText("正在连接坚果云…")

    def _on_test_conn(self) -> None:
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中…")
        self.conn_result.setText("")
        self.ctl.sync.test_connection(
            self.sync_account.text().strip(),
            self.sync_app_pw.text(),
            self.sync_url.text().strip(),
        )

    def _on_conn_tested(self, ok: bool, msg: str) -> None:
        # pending 时按钮虽 disabled 但文本也要复位，避免滞留"测试中…"
        self.test_btn.setText("测试连接")
        if not self._setup_pending:
            self.test_btn.setEnabled(True)
        self.conn_result.setText(("✓ " if ok else "✗ ") + msg)
        self.conn_result.setStyleSheet(
            f"color: {'#2D6550' if ok else '#d64541'}; font-size: 11px;"
        )

    def _on_status_text(self, text: str) -> None:
        self.sync_status.setText(text)
        # 建库/加入 job 失败（网络/凭据）：解锁输入允许重试
        failed = (
            text.startswith("同步失败")
            or text.startswith("错误")
            or "不可用" in text
        )
        if failed and self._setup_pending:
            self._set_sync_pending(False)
            self.setup_btn.setEnabled(True)
            self._sync_setup_label()

    def _on_setup(self) -> None:
        svc = self.ctl.sync
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        vault_pw = self.vault_pw.text()
        if not account:
            self.sync_status.setText("错误：请填写坚果云账号")
            return
        # 账号变更但应用密码留空：queue_setup 会用旧账号凭据建库，
        # 必须先挡住（save_config 也只在账号一致时保留旧密码）
        if not app_pw:
            from .sync import credentials
            try:
                old_acc, _ = credentials.load_credentials()
            except Exception:
                old_acc = None
            if old_acc and old_acc.casefold() != account.casefold():
                self.sync_status.setText(
                    "错误：账号已变更，请重新填写应用密码"
                )
                return
        if not vault_pw:
            self.sync_status.setText(
                "错误：请填写同步库密码（加密密钥，两台设备需相同）"
            )
            return
        if len(vault_pw) < 6:
            self.sync_status.setText("错误：同步库密码至少 6 位")
            return
        bound_vid = svc.vault_id()
        if self.vault_join.isChecked():
            vid = self.vault_id_edit.text().strip().lower()
            try:
                vid = str(uuidlib.UUID(vid))
            except ValueError:
                self.sync_status.setText("错误：同步库 ID 格式不正确")
                return
            if bound_vid and bound_vid != vid:
                self.sync_status.setText(
                    f"错误：本机已绑定同步库 {bound_vid[:8]}…，"
                    "不能静默切换到其他库"
                )
                return
            if bound_vid == vid:
                self.sync_status.setText("本机已绑定该同步库，无需重复加入")
                return
            mode = "join"
        else:
            if bound_vid:
                self.sync_status.setText(
                    "错误：本机已绑定同步库，不要重复新建；"
                    "如需换库请先备份数据"
                )
                return
            vid = str(uuidlib.uuid4())
            mode = "create"
        # 先保存配置（启用同步+凭据），再排队建库/加入
        if not svc.save_config(
            enabled=True,
            base_url=self.sync_url.text().strip()
            or "https://dav.jianguoyun.com/dav/",
            account=account,
            app_password=app_pw,
        ):
            return
        self.sync_enabled.setChecked(True)
        if not svc.queue_setup(mode, vid, vault_pw):
            return  # 凭据缺失，状态栏已提示；不进入 pending
        self._set_sync_pending(True)
        self.sync_status.setText("正在连接坚果云并设置同步库，请稍候…")

    def _show_vault_ready(self, vid: str, announce: bool = True) -> None:
        self._vault_id = vid
        self.vault_info.setText(
            f"同步库已就绪\n库 ID：{vid}\n"
            "数据会同步到坚果云的 memo-sync 目录"
            "（可在坚果云客户端/网页端看到），"
            "另一台设备用相同库 ID + 库密码加入即可。"
        )
        self.vault_box.show()
        self.setup_btn.setEnabled(False)
        self.setup_btn.setText("已绑定同步库 ✓")
        if announce:
            self.sync_status.setText("同步库已就绪，开始首次同步…")

    def _on_vault_ready(self, vid: str) -> None:
        self._set_sync_pending(False)
        self._show_vault_ready(str(vid))

    def _copy_vault_id(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._vault_id)
        self.copy_vid_btn.setText("已复制 ✓")
        # context 重载：对话框销毁后 lambda 不再触发
        QTimer.singleShot(
            1500, self.copy_vid_btn,
            lambda: self.copy_vid_btn.setText("复制库 ID"),
        )

    def _on_save(self) -> None:
        if not self.ctl.set_hotkey(self.hotkey_edit.text()):
            self.tip.setText("快捷键格式无法识别，请重新录制（需修饰键+普通键）")
            self.tip.setStyleSheet("color: #d64541; font-size: 11px;")
            return
        if self._save_sync():
            self.accept()

    def _save_sync(self) -> bool:
        """保存设置：快捷键 + 同步开关/地址/账号。

        建库/加入由第 2 步按钮单独触发（_on_setup），
        此处只持久化非密配置；已绑定库时顺带触发一次同步。
        """
        svc = self.ctl.sync
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        enabled = self.sync_enabled.isChecked()

        if enabled and not account:
            self.sync_status.setText("错误：请填写坚果云账号（第 1 步）")
            return False
        # 与 _on_setup 同理：账号变更+应用密码留空会静默沿用旧凭据
        if enabled and not app_pw:
            from .sync import credentials
            try:
                old_acc, _ = credentials.load_credentials()
            except Exception:
                old_acc = None
            if old_acc and old_acc.casefold() != account.casefold():
                self.sync_status.setText(
                    "错误：账号已变更，请重新填写应用密码"
                )
                return False

        if not svc.save_config(
            enabled=enabled,
            base_url=self.sync_url.text().strip()
            or "https://dav.jianguoyun.com/dav/",
            account=account,
            app_password=app_pw,
        ):
            return False  # 凭据保存失败，状态栏已提示
        if enabled and svc.vault_id():
            svc.sync_now()
        return True
