"""设置对话框：呼出快捷键 + 坚果云同步配置。"""
from __future__ import annotations

import uuid as uuidlib

from PySide6.QtCore import QTimer
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
)


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.ctl = controller
        self._recording = False
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

    # ---------- 同步设置 ----------

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

        self.sync_url = QLineEdit(svc.base_url)
        self.sync_url.setPlaceholderText("https://dav.jianguoyun.com/dav/")
        _row("服务器地址", self.sync_url)

        self.sync_account = QLineEdit()
        self.sync_account.setPlaceholderText("坚果云账号（邮箱）")
        _row("账号", self.sync_account)

        self.sync_app_pw = QLineEdit()
        self.sync_app_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.sync_app_pw.setPlaceholderText("第三方应用密码（留空=不修改）")
        _row("应用密码", self.sync_app_pw)

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
            "同步库 ID（加入模式必填，对方设备可在此复制）"
        )
        self.vault_id_edit.setEnabled(False)
        _row("同步库 ID", self.vault_id_edit)
        self.vault_join.toggled.connect(
            lambda on: self.vault_id_edit.setEnabled(on)
        )

        self.vault_pw = QLineEdit()
        self.vault_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.vault_pw.setPlaceholderText("同步库密码（加密密钥，务必牢记）")
        _row("同步库密码", self.vault_pw)

        self.sync_status = QLabel(svc.status_text)
        self.sync_status.setObjectName("dialogHint")
        self.sync_status.setWordWrap(True)
        lay.addWidget(self.sync_status)
        svc.status_changed.connect(self.sync_status.setText)

        cur_vid = svc.vault_id()
        if cur_vid:
            self.vault_id_edit.setText(cur_vid)
            self.vault_join.setChecked(True)

        try:
            from .sync import credentials
            acc, _pw = credentials.load_credentials() or (None, None)
        except Exception:
            acc = None
        if acc:
            self.sync_account.setText(acc)

        hint = QLabel(
            "首次使用：选「新建同步库」设好密码保存；另一台设备选"
            "「加入已有同步库」并填入相同的库 ID 和库密码。"
            "库密码遗忘则无法找回加密数据。"
        )
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

    def _on_save(self) -> None:
        if not self.ctl.set_hotkey(self.hotkey_edit.text()):
            self.tip.setText("快捷键格式无法识别，请重新录制（需修饰键+普通键）")
            self.tip.setStyleSheet("color: #d64541; font-size: 11px;")
            return
        if self._save_sync():
            self.accept()

    def _save_sync(self) -> bool:
        svc = self.ctl.sync
        account = self.sync_account.text().strip()
        app_pw = self.sync_app_pw.text()
        vault_pw = self.vault_pw.text()
        enabled = self.sync_enabled.isChecked()

        # 凭据完整性：启用时账号必填；库密码由建库/加入流程验证后
        # 才落 keyring（留空=本次不做建库/加入）
        vid = ""
        bound_vid = svc.vault_id()
        if enabled:
            if not account:
                self.sync_status.setText("错误：请填写坚果云账号")
                return False
            if vault_pw and len(vault_pw) < 6:
                self.sync_status.setText("错误：同步库密码至少 6 位")
                return False
            wants_setup = bool(vault_pw)
            if self.vault_join.isChecked():
                vid = self.vault_id_edit.text().strip().lower()
                try:
                    uuidlib.UUID(vid)
                except ValueError:
                    self.sync_status.setText("错误：同步库 ID 格式不正确")
                    return False
                vid = str(uuidlib.UUID(vid))  # 规范化格式
                if bound_vid and bound_vid != vid:
                    self.sync_status.setText(
                        f"错误：本机已绑定同步库 {bound_vid[:8]}…，"
                        "不能静默切换到其他库"
                    )
                    return False
                if not vault_pw and bound_vid is None:
                    # 未绑定 + 加入模式 + 无库密码 = 不完整，不能放行；
                    # 已绑定同库（bound_vid == vid）无需重复验证，放行
                    self.sync_status.setText(
                        "错误：加入已有同步库需要填写同步库密码"
                    )
                    return False
            elif wants_setup:
                if bound_vid:
                    self.sync_status.setText(
                        "错误：本机已绑定同步库，不要重复新建；"
                        "如需换库请先备份数据"
                    )
                    return False
                vid = str(uuidlib.uuid4())  # 新建库：生成 vault_id
            elif self.vault_id_edit.text().strip() and not bound_vid:
                # 填了库 ID 但没填库密码：加入不完整，不能静默降级
                self.sync_status.setText(
                    "错误：加入已有同步库需要填写同步库密码"
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
        if enabled and vid and vault_pw:
            # 建库/加入（网络在工作线程；成功后才保存库密码）
            mode = "join" if self.vault_join.isChecked() else "create"
            svc.queue_setup(mode, vid, vault_pw)
        elif enabled:
            svc.sync_now()
        return True
