"""同步服务（计划 §4.5 调度 + §6 凭据）。

- 所有网络操作在专用 QThread 工作线程执行，GUI 事件不碰网络；
- job 类型：test_conn / setup / sync；
- 调度：启动后延迟首同步、编辑防抖、周期轮询、手动触发；
- 凭据走 credentials.py（Windows Credential Manager），
  vault_id 存 sync_profile，base_url/enabled 存 QSettings。
"""
from __future__ import annotations

import logging
import urllib.parse
import uuid
from datetime import datetime

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal

from .. import db
from . import credentials, engine, protocol, store, vault_setup
from .transport import NotFoundError, WebDAVClient, WebDAVError

DEFAULT_REMOTE_DIR = "memo-app"
CONFLICT_MODES = ("lww", "keep")

logger = logging.getLogger("memo_app.sync.service")

DEFAULT_BASE_URL = "https://dav.jianguoyun.com/dav/"
PERIODIC_MS = 120_000       # 周期拉取间隔
DEBOUNCE_MS = 20_000        # 编辑后防抖上传
STARTUP_DELAY_MS = 8_000    # 启动后首同步延迟


def run_job(job: dict) -> dict:
    """在工作线程执行一个同步 job，返回结果 dict（不抛 GUI 异常）。"""
    kind = job["kind"]
    base_url = job["base_url"]
    account = job["account"]
    app_pw = job["app_password"]

    if kind == "test_conn":
        # 探测 WebDAV 根目录：拿到任何 HTTP 响应（207/404/409）
        # 都说明认证通过；只有 401/403 或网络错误才算失败
        probe = WebDAVClient(
            base_url, account, app_pw, root_dir="", timeout=15,
            allow_http=bool(job.get("allow_http")),
        )
        try:
            probe.propfind("", depth=0)
        except NotFoundError:
            pass  # 认证通过
        return {"ok": True, "kind": kind}

    vault_pw = job["vault_password"]
    with db.db() as conn:
        writer_id = store.profile_writer_id(conn)
        vault_id = store.profile_vault_id(conn)

    if kind == "setup":
        # Obsidian 式初始化：远端有 vault.json → 加入（vault_id 从
        # 配置读出，不手输）；没有 → 新建。远程目录取自设置。
        client = WebDAVClient(
            base_url, account, app_pw, root_dir=job["remote_dir"],
            timeout=30, allow_http=bool(job.get("allow_http")),
        )
        if client.exists(protocol.VAULT_FILE):
            mk, config = vault_setup.open_remote_vault(client, vault_pw)
            vid = config.get("vault_id")
            if not protocol.is_uuid(vid):
                raise WebDAVError(
                    "远端不是有效的同步库（vault.json 缺少 vault_id）"
                )
        else:
            vid = str(uuid.uuid4())
            mk = vault_setup.create_vault(client, vid, writer_id, vault_pw)
        # 库密码先于 vault_id 落库：keyring 写失败时本机保持未绑定，
        # 用户可重试；反过来会造成"已绑定但无密码"的不可自助恢复状态
        credentials.save_vault_password(vault_pw)
        with db.db() as conn:
            store.set_profile_vault_id(conn, vid)
        return {"ok": True, "kind": kind, "vault_id": vid}

    # sync：vault_id 必须已配置
    if not vault_id:
        return {"ok": False, "kind": kind,
                "error": "未配置同步库（vault）"}
    client = WebDAVClient(
        base_url, account, app_pw, root_dir=job["remote_dir"],
        timeout=30, allow_http=bool(job.get("allow_http")),
    )
    mk, config = vault_setup.open_remote_vault(client, vault_pw)
    # 交叉校验：远端 vault.json 内嵌的 vault_id 必须与本机绑定一致，
    # 否则本地批次的 AAD 上下文对不上，会产生对端无法解密的数据。
    # 旧版库 vault.json 无 vault_id 字段，跳过校验。
    remote_vid = str(config.get("vault_id") or "").lower()
    local_vid = str(vault_id).lower()
    if remote_vid and protocol.is_uuid(remote_vid) \
            and remote_vid != local_vid:
        raise WebDAVError("远程目录不是当前绑定的同步库")
    eng = engine.Engine(
        client, mk, local_vid,
        conflict_mode=job.get("conflict_mode", "lww"),
    )
    stats = eng.sync_once()
    return {"ok": True, "kind": kind, "stats": stats}


class _Worker(QObject):
    job = Signal(dict)
    result = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.job.connect(self._run)

    def _run(self, j: dict) -> None:
        try:
            self.result.emit(run_job(j))
        except Exception as e:
            # 只记 hostname：base_url 是用户输入，可能内嵌凭据
            host = urllib.parse.urlsplit(str(j.get("base_url"))).hostname
            logger.warning(
                "sync job failed: kind=%s host=%s err=%s",
                j.get("kind"), host, e,
            )
            self.result.emit({
                "ok": False, "kind": j.get("kind"),
                "error": f"{type(e).__name__}: {e}",
            })


class SyncService(QObject):
    status_changed = Signal(str)
    synced = Signal()   # 远端数据已应用到本地库，UI 需刷新
    conn_tested = Signal(bool, str)  # (成功?, 结果说明) 连接测试
    vault_ready = Signal(str)        # 同步库就绪，参数=vault_id

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings()
        self._status = "未启用"
        self._inflight = 0  # 已入队/执行中的 job 数（>0 即忙）
        self._setup_busy = False  # setup job 进行中（防重复建库）
        self._login_candidate = None
        self.directory_cache = {}
        self._pending = False
        self._pending_mode = "lww"
        self._thread = QThread(self)
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        self._worker.result.connect(self._on_result)
        self._thread.start()

        # 周期 + 防抖 + 启动延迟
        self._periodic = QTimer(self)
        self._periodic.setInterval(PERIODIC_MS)
        self._periodic.timeout.connect(lambda: self.request_sync())
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(lambda: self.request_sync())
        # 重启恢复：已启用同步时周期调度必须立即恢复，
        # 不能依赖用户重新打开设置（SYNC-003）
        if self.enabled:
            self._periodic.start()
        QTimer.singleShot(STARTUP_DELAY_MS, self._maybe_autostart)

    # ---------- 状态查询 ----------

    @property
    def enabled(self) -> bool:
        return bool(self._settings.value("sync_enabled", False))

    @property
    def base_url(self) -> str:
        return str(
            self._settings.value("sync_base_url", DEFAULT_BASE_URL)
        )

    @property
    def remote_dir(self) -> str:
        """vault 根目录（base_url 相对路径，"" = WebDAV 根）。

        未显式设置过 sync_remote_dir 且本机已绑定库时，回退旧版
        memo-sync/<vault_id> 布局——保证升级前建库的用户不断同步。
        """
        rd = self._settings.value("sync_remote_dir")
        if rd is not None:
            return str(rd)
        vid = self.vault_id()
        if vid:
            return f"{protocol.REMOTE_ROOT}/{vid}"
        return DEFAULT_REMOTE_DIR

    @property
    def conflict_mode(self) -> str:
        """冲突解决策略：lww 后写覆盖 / keep 保留冲突副本。"""
        m = str(self._settings.value("sync_conflict_mode", "lww"))
        return m if m in CONFLICT_MODES else "lww"

    def vault_id(self) -> str | None:
        try:
            with db.db() as conn:
                return store.profile_vault_id(conn)
        except Exception:
            return None

    @property
    def status_text(self) -> str:
        return self._status

    def _set_status(self, text: str) -> None:
        self._status = text
        self.status_changed.emit(text)

    # ---------- 配置 ----------

    def save_config(
        self,
        *,
        enabled: bool,
        base_url: str,
        account: str,
        app_password: str,
        remote_dir: str | None = None,
        conflict_mode: str = "",
    ) -> bool:
        """保存非敏感配置 + 账号/应用密码（不触发网络）。

        app_password 留空 = 不修改已存密码（UI 承诺语义）；
        只改账号时仅在账号未变更的前提下保留原应用密码
        （应用密码是账号级凭据，换账号必须重填）。
        返回 False 表示凭据保存失败（调用方应中止后续动作）。
        """
        base_url = base_url.strip() or DEFAULT_BASE_URL
        if remote_dir is not None:
            rd = remote_dir.strip().strip("/")
            # 本地校验（不触网）：构造客户端时非法路径直接抛错；
            # 必须先于凭据写入，避免"配置被拒但凭据已落库"
            try:
                WebDAVClient(base_url, "u", "p", root_dir=rd)
            except WebDAVError as e:
                self._set_status(f"远程目录无效：{e}")
                return False
        try:
            if app_password and account.strip():
                credentials.save_credentials(account, app_password)
            elif account:
                old_acc, old_pw = credentials.load_credentials()
                if old_pw and old_acc and \
                        old_acc.casefold() == account.casefold():
                    credentials.save_credentials(account, old_pw)
        except credentials.CredentialError as e:
            self._set_status(f"安全存储不可用：{e}")
            return False
        if remote_dir is not None:
            self._settings.setValue("sync_remote_dir", rd)
        if conflict_mode in CONFLICT_MODES:
            self._settings.setValue("sync_conflict_mode", conflict_mode)
        self._settings.setValue("sync_base_url", base_url)
        self._settings.setValue("sync_enabled", enabled)
        if enabled:
            self._periodic.start()
        else:
            self._periodic.stop()
            self._debounce.stop()
        self._set_status("已启用，等待同步" if enabled else "已停用")
        return True

    def test_connection(
        self, account: str, app_password: str, base_url: str = ""
    ) -> None:
        """验证坚果云账号+应用密码（真实网络请求，工作线程）。

        app_password 留空时回退到已存凭据——但仅当账号与已存
        账号一致；账号变更必须重新填写应用密码。
        """
        if self._inflight:
            self.conn_tested.emit(False, "正在同步中，请稍后再试")
            return
        account = account.strip()
        if not app_password:
            try:
                acc, pw = credentials.load_credentials()
            except credentials.CredentialError:
                acc = pw = None
            if not account:
                account = acc or ""
            if pw and account and acc and \
                    acc.casefold() == account.casefold():
                app_password = pw
            elif pw and account:
                self.conn_tested.emit(
                    False, "账号已变更，请重新填写应用密码"
                )
                return
        if not account or not app_password:
            self.conn_tested.emit(False, "请先填写账号和应用密码")
            return
        self._login_candidate = (
            account, app_password, base_url.strip() or self.base_url,
        )
        self._enqueue({
            "kind": "test_conn",
            "base_url": base_url.strip() or self.base_url,
            "account": account,
            "app_password": app_password,
        })

    def queue_setup(self, vault_password: str) -> bool:
        """排队执行同步库初始化（自动判断新建/加入，Obsidian 式）。

        远端目录有 vault.json → 用库密码加入；没有 → 新建库。
        返回 True 表示已入队；False 表示凭据缺失/已有任务未入队。
        """
        if self._setup_busy:
            self._set_status("同步库设置正在进行中，请稍候")
            return False
        # 已绑定库时禁止重复初始化：换目录会覆盖 vault 绑定，
        # 旧批次按旧 vault_id 加密后对端永远解不开（纵深防御，
        # UI 层 _on_setup 已有同样拦截）
        if self.vault_id():
            self._set_status("本机已绑定同步库，重复初始化已拒绝")
            return False
        try:
            account, app_pw = credentials.load_credentials()
        except credentials.CredentialError as e:
            self._set_status(f"安全存储不可用：{e}")
            return False
        if not account or not app_pw:
            self._set_status("错误：缺少坚果云账号/应用密码")
            return False
        # 固化本次 setup 使用的远程目录：未绑定时 remote_dir 可能是
        # 默认值而非显式设置，绑定后属性会回退旧布局——不同步固化
        # 会导致 setup 建在 A 目录、sync 却去 B 目录找 vault.json
        self._settings.setValue("sync_remote_dir", self.remote_dir)
        self._setup_busy = True
        self._enqueue({
            "kind": "setup",
            "base_url": self.base_url,
            "remote_dir": self.remote_dir,
            "account": account,
            "app_password": app_pw,
            "vault_password": vault_password,
        })
        return True

    def logout(self) -> None:
        """退出登录：清除账号凭据并停用同步（远程目录/库密码保留）。"""
        self._login_candidate = None
        self.directory_cache.clear()
        try:
            credentials.clear_credentials()
        except credentials.CredentialError as e:
            self._set_status(f"退出登录失败：{e}")
            return
        self._settings.setValue("sync_enabled", False)
        self._periodic.stop()
        self._debounce.stop()
        self._set_status("已退出登录")

    # ---------- 同步触发 ----------

    def request_sync(self, conflict_mode: str = "") -> None:
        """请求一轮同步；运行中则合并为结束后补一次。

        conflict_mode: "lww" 后写覆盖 / "keep" 保留冲突副本；
        空串时使用设置里的冲突解决策略。
        """
        if not self.enabled:
            return
        mode = (
            conflict_mode if conflict_mode in CONFLICT_MODES
            else self.conflict_mode
        )
        try:
            with db.db() as conn:
                if store.profile_vault_id(conn) is None:
                    self._set_status("未配置同步库，请在设置中完成初始化")
                    return
            account, app_pw = credentials.load_credentials()
            vault_pw = credentials.load_vault_password()
        except credentials.CredentialError as e:
            self._set_status(f"安全存储不可用：{e}")
            return
        except Exception:
            return
        if not account or not app_pw or not vault_pw:
            self._set_status("错误：凭据不完整，请检查设置")
            return
        if self._inflight:
            # 记录本次请求的冲突策略，任务结束后补跑时保留；
            # keep 优先——用户显式选择的保留语义不被自动 lww 覆盖
            self._pending = True
            if mode == "keep" or self._pending_mode != "keep":
                self._pending_mode = mode
            return
        self._enqueue({
            "kind": "sync",
            "base_url": self.base_url,
            "remote_dir": self.remote_dir,
            "account": account,
            "app_password": app_pw,
            "vault_password": vault_pw,
            "conflict_mode": mode,
        })

    def notify_change(self) -> None:
        """本地有业务写入：防抖后触发同步。

        memos_changed 也在搜索/刷新时触发——先查是否真有
        待发变更，避免空闲消耗 WebDAV 请求预算。
        """
        if not self.enabled:
            return
        try:
            with db.db() as conn:
                wid = store.profile_writer_id(conn)
                has_work = (
                    store.has_unpublished(conn, wid)
                    or store.pending_outbox(conn, 1)
                )
        except Exception:
            return
        if has_work:
            self._debounce.start()

    def sync_now(self, conflict_mode: str = "") -> None:
        """手动立即同步（忽略防抖，仍走工作线程）。"""
        self._debounce.stop()
        self.request_sync(conflict_mode)

    # ---------- 内部 ----------

    def _maybe_autostart(self) -> None:
        if self.enabled:
            self.request_sync()

    def _enqueue(self, job: dict) -> None:
        self._inflight += 1
        self._worker.job.emit(job)

    def _on_result(self, res: dict) -> None:
        self._inflight = max(0, self._inflight - 1)
        kind = res.get("kind")
        if kind == "setup":
            self._setup_busy = False
        if kind == "test_conn":
            candidate = self._login_candidate
            self._login_candidate = None
            if res.get("ok") and candidate:
                account, app_pw, base_url = candidate
                try:
                    credentials.save_credentials(account, app_pw)
                except credentials.CredentialError as e:
                    self.conn_tested.emit(False, f"连接成功，但账号保存失败：{e}")
                else:
                    self._settings.setValue("sync_base_url", base_url)
                    self.conn_tested.emit(True, "连接成功，账号已安全保存")
            else:
                self.conn_tested.emit(
                    False, f"连接失败：{res.get('error', '未知错误')}"
                )
        elif res.get("ok"):
            if kind == "sync":
                s = res.get("stats", {})
                ts = datetime.now().strftime("%H:%M")
                bad = s.get("bad_ops", 0)
                copies = s.get("conflict_copies", 0)
                suffix = f"（{bad} 条数据异常已跳过）" if bad else ""
                if copies:
                    suffix += f"（{copies} 个冲突副本已保留）"
                self._set_status(
                    f"已同步 {ts}（拉取 {s.get('pulled', 0)} 批 / "
                    f"应用 {s.get('applied_ops', 0)} 条 / "
                    f"发布 {s.get('published', 0)} 批）{suffix}"
                )
                if s.get("applied_ops", 0) > 0 or copies > 0:
                    # 远端数据/冲突副本落库后通知 UI 刷新
                    # （编辑缓冲由 reload 保护）
                    self.synced.emit()
            else:
                self._set_status("同步库已就绪")
                vid = res.get("vault_id")
                if vid:
                    self.vault_ready.emit(str(vid))
                # 建库/加入成功后立即同步，不等周期轮询
                self.request_sync()
        else:
            err = res.get("error", "未知错误")
            self._set_status(f"同步失败：{err}")
            logger.warning("sync %s failed: %s", kind, err)
        if self._pending:
            self._pending = False
            mode = self._pending_mode
            self._pending_mode = "lww"
            self.request_sync(mode)

    def shutdown(self) -> None:
        self._periodic.stop()
        self._debounce.stop()
        self._thread.quit()
        # 单轮 sync 最坏可达数十秒（多请求×30s timeout）；
        # 等 8s 后仍未结束则 terminate——SQLite WAL 可从中断恢复
        if not self._thread.wait(8000):
            logger.warning("sync thread did not finish, terminating")
            self._thread.terminate()
            self._thread.wait(2000)
