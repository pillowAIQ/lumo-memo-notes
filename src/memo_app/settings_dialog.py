"""设置对话框：呼出快捷键 + 坚果云同步配置（仿 Obsidian Sync）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .remote_dir_dialog import RemoteDirDialog
from .sync.transport import WebDAVError


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

    # ---------- 同步设置（仿 Obsidian Sync：提醒/账号/通用） ----------

    def _build_sync_section(self, lay) -> None:
        svc = self.ctl.sync
        sep = QLabel("同步（坚果云 WebDAV + 端到端加密）")
        sep.setObjectName("dialogTitle")
        lay.addWidget(sep)

        # ── 备份提醒 ────────────────────────────────────
        warn = QLabel(
            "⚠ 注意：同步过程会修改或删除本地数据，"
            "建议在同步前备份重要文件。"
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b8860b; font-size: 11px;")
        lay.addWidget(warn)

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

        # ── 账号设置 ────────────────────────────────────
        _step("账号")
        mode = QLabel("登录方式：账号 + 应用密码（坚果云第三方应用密码）")
        mode.setObjectName("dialogHint")
        lay.addWidget(mode)

        # 已登录状态行（有凭据时显示）
        self.login_row = QWidget()
        lr = QHBoxLayout(self.login_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(8)
        self.login_label = QLabel("")
        lr.addWidget(self.login_label, 1)
        self.logout_btn = QPushButton("退出登录")
        self.logout_btn.clicked.connect(self._on_logout)
        lr.addWidget(self.logout_btn)
        lay.addWidget(self.login_row)

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
            "第三方应用密码（留空则沿用已保存的）"
        )
        pw_row.addWidget(self.sync_app_pw, 1)
        self.test_btn = QPushButton("检查连接")
        self.test_btn.clicked.connect(self._on_test_conn)
        pw_row.addWidget(self.test_btn)
        lay.addLayout(pw_row)

        self.conn_result = QLabel("")
        self.conn_result.setWordWrap(True)
        lay.addWidget(self.conn_result)

        # ── 通用设置 ────────────────────────────────────
        _step("通用")

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(QLabel("远程目录"))
        self.remote_dir_edit = QLineEdit("/" + svc.remote_dir if svc.remote_dir else "/")
        self.remote_dir_edit.setPlaceholderText("/我的坚果云/memo")
        dir_row.addWidget(self.remote_dir_edit, 1)
        self.browse_btn = QPushButton("浏览…")
        self.browse_btn.clicked.connect(self._on_browse)
        dir_row.addWidget(self.browse_btn)
        lay.addLayout(dir_row)

        self.vault_pw = QLineEdit()
        self.vault_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.vault_pw.setPlaceholderText(
            "同步库密码（端到端加密密钥，两台设备必须相同，务必牢记）"
        )
        _row("同步库密码", self.vault_pw)

        setup_row = QHBoxLayout()
        setup_row.addStretch(1)
        self.setup_btn = QPushButton("初始化同步库")
        self.setup_btn.setToolTip(
            "远程目录已有同步库则加入；没有则创建新库"
        )
        self.setup_btn.clicked.connect(self._on_setup)
        setup_row.addWidget(self.setup_btn)
        lay.addLayout(setup_row)

        strat_row = QHBoxLayout()
        strat_row.setSpacing(8)
        strat_row.addWidget(QLabel("冲突解决策略"))
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("后写覆盖（推荐）", "lww")
        self.conflict_combo.addItem("保留冲突副本", "keep")
        idx = self.conflict_combo.findData(svc.conflict_mode)
        if idx >= 0:
            self.conflict_combo.setCurrentIndex(idx)
        strat_row.addWidget(self.conflict_combo, 1)
        lay.addLayout(strat_row)
        strat_hint = QLabel(
            "两台设备同时修改同一条备忘录时的处理方式；"
            "点标题栏「同步 → 立即同步」时也可以临时选择。"
        )
        strat_hint.setObjectName("dialogHint")
        strat_hint.setWordWrap(True)
        lay.addWidget(strat_hint)

        # 库就绪信息（已绑定时显示远程目录说明）
        self.vault_box = QWidget()
        vb = QVBoxLayout(self.vault_box)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(4)
        self.vault_info = QLabel("")
        self.vault_info.setWordWrap(True)
        vb.addWidget(self.vault_info)
        self.vault_box.hide()
        lay.addWidget(self.vault_box)

        # ── 状态 ────────────────────────────────────────
        _step("同步状态")
        self.sync_status = QLabel(svc.status_text)
        self.sync_status.setObjectName("dialogHint")
        self.sync_status.setWordWrap(True)
        lay.addWidget(self.sync_status)
        svc.status_changed.connect(self._on_status_text)
        svc.conn_tested.connect(self._on_conn_tested)
        svc.vault_ready.connect(self._on_vault_ready)

        self._vault_id = ""
        self._setup_pending = False  # 初始化 job 进行中
        # 异步任务期间锁定的输入控件
        self._sync_inputs = [
            self.sync_enabled, self.sync_url, self.sync_account,
            self.sync_app_pw, self.test_btn, self.vault_pw,
            self.remote_dir_edit, self.browse_btn,
            self.conflict_combo, self.logout_btn,
        ]

        cur_vid = svc.vault_id()
        if cur_vid:
            self._show_vault_ready(cur_vid, announce=False)
        self._refresh_login_state()

        hint = QLabel(
            "填好账号后点「检查连接」验证；再选远程目录、设同步库密码，"
            "点「初始化同步库」——远程已有库会自动加入，没有会自动创建。"
            "同步库密码遗忘则无法找回加密数据。"
        )
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

    # ---------- 同步引导动作 ----------

    def _set_sync_pending(self, on: bool) -> None:
        """初始化进行中锁定同步输入，防止表单与实际 job 不一致。"""
        self._setup_pending = on
        for w in self._sync_inputs:
            w.setEnabled(not on)
        if on:
            self.setup_btn.setEnabled(False)
            self.setup_btn.setText("正在连接坚果云…")
        elif not self._vault_id:
            self.setup_btn.setEnabled(True)
            self.setup_btn.setText("初始化同步库")

    def _on_test_conn(self) -> None:
        self.test_btn.setEnabled(False)
        self.test_btn.setText("检查中…")
        self.conn_result.setText("")
        self.ctl.sync.test_connection(
            self.sync_account.text().strip(),
            self.sync_app_pw.text(),
            self.sync_url.text().strip(),
        )

    def _on_conn_tested(self, ok: bool, msg: str) -> None:
        # pending 时按钮虽 disabled 但文本也要复位，避免滞留"检查中…"
        self.test_btn.setText("检查连接")
        if not self._setup_pending:
            self.test_btn.setEnabled(True)
        self.conn_result.setText(("✓ " if ok else "✗ ") + msg)
        self.conn_result.setStyleSheet(
            f"color: {'#2D6550' if ok else '#d64541'}; font-size: 11px;"
        )

    def _on_status_text(self, text: str) -> None:
        self.sync_status.setText(text)
        # 初始化 job 失败（网络/凭据）：解锁输入允许重试
        failed = (
            text.startswith("同步失败")
            or text.startswith("错误")
            or "不可用" in text
        )
        if failed and self._setup_pending:
            self._set_sync_pending(False)

    def _refresh_login_state(self) -> None:
        """按凭据管理器状态刷新"已登录"行（仿 Obsidian 账号区）。"""
        from .sync import credentials
        try:
            acc, _pw = credentials.load_credentials()
        except Exception:
            acc = None
        if acc:
            self.login_label.setText(f"已登录：{acc}")
            self.login_row.show()
            if not self.sync_account.text().strip():
                self.sync_account.setText(acc)
        else:
            self.login_row.hide()

    def _on_logout(self) -> None:
        self.ctl.sync.logout()
        self.sync_enabled.setChecked(False)
        self._refresh_login_state()
        self.sync_status.setText("已退出登录")

    def _on_browse(self) -> None:
        """打开远程目录选择器；需要账号凭据（字段或已存）。"""
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        if not app_pw:
            from .sync import credentials
            try:
                acc, pw = credentials.load_credentials()
            except Exception:
                acc = pw = None
            if pw and acc and (
                not account or acc.casefold() == account.casefold()
            ):
                account = account or acc
                app_pw = pw
        if not account or not app_pw:
            self.sync_status.setText("错误：请先填写账号和应用密码")
            return
        try:
            dlg = RemoteDirDialog(
                self.sync_url.text().strip()
                or "https://dav.jianguoyun.com/dav/",
                account,
                app_pw,
                self.remote_dir_edit.text().strip("/"),
                self,
            )
        except WebDAVError as e:
            # 构造时同步校验 base_url/凭据格式，非法输入直接报错
            self.sync_status.setText(f"错误：{e}")
            return
        if dlg.exec() == QDialog.DialogCode.Accepted:
            rd = dlg.result_dir()
            self.remote_dir_edit.setText("/" + rd if rd else "/")

    def _on_setup(self) -> None:
        """初始化同步库：远端有 vault.json 自动加入，没有自动创建。"""
        svc = self.ctl.sync
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        vault_pw = self.vault_pw.text()
        if not account:
            self.sync_status.setText("错误：请填写坚果云账号")
            return
        # 应用密码留空仅当"已存凭据属于同一账号"才可沿用；
        # 首次使用或账号变更必须重填，否则 save_config 不落凭据
        # 但 enabled 已持久化，会留下"已启用却无凭据"的半状态
        if not app_pw:
            from .sync import credentials
            try:
                old_acc, old_pw = credentials.load_credentials()
            except Exception:
                old_acc = old_pw = None
            if not (
                old_pw and old_acc
                and old_acc.casefold() == account.casefold()
            ):
                self.sync_status.setText(
                    "错误：请填写应用密码（首次使用或账号变更时必填）"
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
        if svc.vault_id():
            self.sync_status.setText("本机已绑定同步库，无需重复初始化")
            return
        # 先保存配置（启用同步+凭据+远程目录+冲突策略），再排队初始化
        if not svc.save_config(
            enabled=True,
            base_url=self.sync_url.text().strip()
            or "https://dav.jianguoyun.com/dav/",
            account=account,
            app_password=app_pw,
            remote_dir=self.remote_dir_edit.text(),
            conflict_mode=self.conflict_combo.currentData(),
        ):
            return
        self.sync_enabled.setChecked(True)
        if not svc.queue_setup(vault_pw):
            return  # 凭据缺失，状态栏已提示；不进入 pending
        self._set_sync_pending(True)
        self.sync_status.setText("正在连接坚果云并初始化同步库，请稍候…")

    def _show_vault_ready(self, vid: str, announce: bool = True) -> None:
        self._vault_id = vid
        rd = "/" + self.ctl.sync.remote_dir if self.ctl.sync.remote_dir else "/"
        self.vault_info.setText(
            f"同步库已绑定\n远程目录：{rd}\n"
            "数据会同步到坚果云该目录（可在坚果云客户端/网页端看到），"
            "另一台设备选相同远程目录 + 相同同步库密码即可加入。"
        )
        self.vault_box.show()
        self.setup_btn.setEnabled(False)
        self.setup_btn.setText("已绑定同步库 ✓")
        if announce:
            self.sync_status.setText("同步库已就绪，开始首次同步…")

    def _on_vault_ready(self, vid: str) -> None:
        self._set_sync_pending(False)
        self._show_vault_ready(str(vid))

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
        # 与 _on_setup 同理：应用密码留空仅当已存凭据属于同一账号
        if enabled and not app_pw:
            from .sync import credentials
            try:
                old_acc, old_pw = credentials.load_credentials()
            except Exception:
                old_acc = old_pw = None
            if not (
                old_pw and old_acc
                and old_acc.casefold() == account.casefold()
            ):
                self.sync_status.setText(
                    "错误：请填写应用密码（首次使用或账号变更时必填）"
                )
                return False

        if not svc.save_config(
            enabled=enabled,
            base_url=self.sync_url.text().strip()
            or "https://dav.jianguoyun.com/dav/",
            account=account,
            app_password=app_pw,
            remote_dir=self.remote_dir_edit.text(),
            conflict_mode=self.conflict_combo.currentData(),
        ):
            return False  # 凭据保存失败，状态栏已提示
        if enabled and svc.vault_id():
            svc.sync_now()
        return True
