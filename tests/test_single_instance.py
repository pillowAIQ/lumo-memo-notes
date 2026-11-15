"""单实例守卫测试：真实子进程 + 真实 Qt 本地 IPC/锁 + 临时数据目录。

运行：./.venv/Scripts/python.exe -m unittest discover -s tests -p test_single_instance.py
不碰真实数据、托盘、热键；GUI 集成子进程用 offscreen 平台 + 最小外部服务桩。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memo_app import single_instance  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
MARKER = "activated.marker"

# 子进程模式（SI_MODE 环境变量选择）：
#   plain         只跑 acquire()，primary 时收到激活写 marker，LIFE_MS 后退出
#   hold          只持锁不监听，LIFE_MS 后退出（模拟"锁在、通道未就绪/已死"）
#   gui_primary   走真实 __main__.main()：真 MainWindow + 真 Controller
#                 （仅打桩 pynput/托盘/SyncService/QSettings/提醒），
#                 接收 cmd.txt 指令先隐藏/最小化，激活时记录窗口恢复结果
#   gui_secondary 走真实 __main__.main()：MainWindow/Controller 换成
#                 写标记文件的哨兵，验证次实例绝不构造应用服务
CHILD = r"""
import os, sys, json, pathlib, time
import memo_app  # DLL 预加载须先于 PySide6 导入
mode = os.environ.get("SI_MODE", "plain")
dd = pathlib.Path(os.environ["MEMO_APP_DATA_DIR"])

if mode == "hold":
    from PySide6.QtCore import QSharedMemory
    shm = QSharedMemory(__import__("memo_app.single_instance", fromlist=["x"]).server_name(dd))
    assert shm.create(1), "hold child could not take lock"
    print("holding", flush=True)
    time.sleep(int(os.environ.get("LIFE_MS", "8000")) / 1000)
elif mode == "handover":
    # 挑战者：包一层 _send_activate 记录"已进入既有持有者重试路径"，
    # 然后调用真实实现（不 stub 任何所有权决策）。
    from PySide6.QtCore import QCoreApplication, QTimer
    from memo_app import single_instance as si
    real_send = si._send_activate
    entered = {"n": 0}
    def spy(name, timeout_ms=si._CONNECT_MS):
        if entered["n"] == 0:
            (dd / "retry_entered.marker").write_text("1")
        entered["n"] += 1
        return real_send(name, timeout_ms)
    si._send_activate = spy
    app = QCoreApplication(sys.argv)
    acq = si.acquire()
    (dd / "retry_count.txt").write_text(str(entered["n"]))
    print(acq.role, flush=True)
    if acq.role == "primary":
        marker = dd / "activated.marker"
        acq.guard.activated.connect(
            lambda: marker.write_text("yes", encoding="utf-8"))
        QTimer.singleShot(int(os.environ.get("LIFE_MS", "8000")), app.quit)
        sys.exit(app.exec())
elif mode == "gui_secondary":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import memo_app.__main__ as m
    def sentinel(name):
        def ctor(*a, **k):
            (dd / name).write_text("constructed")
            raise AssertionError(f"{name} constructed in secondary")
        return ctor
    m.MainWindow = sentinel("win_ctor.marker")
    m.Controller = sentinel("ctl_ctor.marker")
    m.main()  # 次实例应直接返回，不构造任何应用服务
    print("secondary-returned", flush=True)
elif mode == "gui_primary":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from unittest.mock import patch
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QSystemTrayIcon
    import memo_app.__main__ as m

    class SyncStub:  # 对齐 ui_smoke 的最小同步桩：无网络/无凭据
        enabled = False
        base_url = "https://dav.jianguoyun.com/dav/"
        remote_dir = "memo-app"
        conflict_mode = "lww"
        status_text = "未启用"
        directory_cache = {}
        class _Sig:
            def connect(self, *a, **k): pass
        status_changed = _Sig()
        conn_tested = _Sig()
        vault_ready = _Sig()
        synced = _Sig()
        def __init__(self, parent=None): pass
        def shutdown(self): pass
        def vault_id(self): return None
        def save_config(self, **kw): return True
        def queue_setup(self, *a, **k): pass
        def sync_now(self, *a, **k): pass
        def test_connection(self, *a, **k): pass
        def logout(self): pass
        def notify_change(self): pass

    OrigCtl = m.Controller

    def ctl_factory(win):
        ctl = OrigCtl(win)          # 真实 Controller.show_main 走真实恢复路径
        ctl._remind_timer.stop()
        orig = ctl.show_main
        def wrapped():
            pre = {"visible": win.isVisible(), "minimized": win.isMinimized()}
            orig()                 # 真实恢复：show + 去最小化 + raise/activate
            (dd / "activated.marker").write_text("yes")
            (dd / "state.json").write_text(json.dumps({
                "pre": pre,
                "post": {"visible": win.isVisible(),
                         "minimized": win.isMinimized()}}))
        ctl.show_main = wrapped
        cmd = dd / "cmd.txt"
        def poll():  # 测试进程通过 cmd.txt 让主窗口先进入隐藏/最小化
            if cmd.exists():
                c = cmd.read_text().strip()
                if c not in ("hide", "min"):
                    return  # 测试进程写入中途读到空内容，下拍再试
                cmd.unlink()
                if c == "hide":
                    win.hide()
                else:
                    win.showMinimized()
                # 状态变化需一个事件循环节拍后才在 offscreen 下生效
                QTimer.singleShot(300, lambda: (dd / "pre.json").write_text(
                    json.dumps({"cmd": c,
                                "visible": win.isVisible(),
                                "hidden": win.isHidden(),
                                "minimized": win.isMinimized()})))
        t = QTimer(ctl); t.timeout.connect(poll); t.start(50)
        QTimer.singleShot(600, lambda: print("READY", flush=True))
        return ctl

    with (
        patch("pynput.keyboard.Listener"),
        patch("memo_app.sync.service.SyncService", SyncStub),
        patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False),
        patch.object(QSettings, "value", return_value="ctrl+o"),
        patch.object(OrigCtl, "_check_reminders"),
    ):
        m.Controller = ctl_factory
        try:
            m.main()
        except SystemExit:
            pass
else:  # plain
    from PySide6.QtCore import QCoreApplication, QTimer
    from memo_app import single_instance
    app = QCoreApplication(sys.argv)
    acq = single_instance.acquire(
        retries=int(os.environ.get("SI_RETRIES", "4")))
    print(acq.role, flush=True)
    if acq.role == "primary":
        marker = dd / "activated.marker"
        acq.guard.activated.connect(
            lambda: marker.write_text("yes", encoding="utf-8"))
        QTimer.singleShot(int(os.environ.get("LIFE_MS", "8000")), app.quit)
        sys.exit(app.exec())
"""


def launch(data_dir: str, life_ms: int = 8000, mode: str = "plain",
           retries: int = 4) -> subprocess.Popen:
    env = dict(os.environ)
    env["MEMO_APP_DATA_DIR"] = data_dir
    env["LIFE_MS"] = str(life_ms)
    env["SI_MODE"] = mode
    env["SI_RETRIES"] = str(retries)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def read_role(proc: subprocess.Popen, timeout: float = 60.0) -> str:
    """读子进程首行角色（不等待退出，超时可靠）。"""
    q: queue.Queue = queue.Queue()
    threading.Thread(
        target=lambda: q.put(proc.stdout.readline()), daemon=True
    ).start()
    try:
        line = q.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError("child produced no role within timeout")
    if line.strip():
        return line.strip()
    raise AssertionError(f"child exited without role; stderr={proc.stderr.read()}")


def wait_file(path: Path, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


class SingleInstanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmps: list[tempfile.TemporaryDirectory] = []
        self._procs: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for p in self._procs:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=10)
            for pipe in (p.stdout, p.stderr):
                if pipe is not None:
                    pipe.close()
        for t in self._tmps:
            for _ in range(30):  # 被杀子进程释放 app.log 句柄需要片刻
                try:
                    t.cleanup()
                    break
                except (PermissionError, NotADirectoryError):
                    time.sleep(0.2)

    def tmpdir(self) -> str:
        t = tempfile.TemporaryDirectory()
        self._tmps.append(t)
        return t.name

    def spawn(self, data_dir: str, life_ms: int = 8000, mode: str = "plain") -> subprocess.Popen:
        p = launch(data_dir, life_ms, mode)
        self._procs.append(p)
        return p

    # ---- 竞态：并发首启只有一个 primary ----
    def test_concurrent_first_launch_single_primary(self) -> None:
        d = self.tmpdir()
        procs = [self.spawn(d, life_ms=4000) for _ in range(3)]
        roles = sorted(read_role(p) for p in procs)
        self.assertEqual(roles.count("primary"), 1, roles)
        self.assertEqual(roles.count("secondary"), 2, roles)
        for p in procs:
            p.wait(timeout=30)
            self.assertEqual(p.returncode, 0)

    # ---- 二次启动：请求激活已运行的主实例并成功退出 ----
    def test_second_launch_activates_primary(self) -> None:
        d = self.tmpdir()
        primary = self.spawn(d)
        self.assertEqual(read_role(primary), "primary")
        secondary = self.spawn(d)
        self.assertEqual(read_role(secondary), "secondary")
        self.assertEqual(secondary.wait(timeout=30), 0)
        # 主实例收到激活消息（写 marker）
        self.assertTrue(wait_file(Path(d) / MARKER))
        # 主实例仍在运行；次实例未在数据目录写任何文件
        self.assertIsNone(primary.poll())
        self.assertEqual(sorted(os.listdir(d)), [MARKER])

    # ---- 隐藏/最小化回调集成：activated 信号驱动 show_main 类回调 ----
    def test_activation_signal_drives_callback(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
        d = self.tmpdir()
        acq = single_instance.acquire(d)
        self.assertEqual(acq.role, "primary")
        calls: list[bool] = []

        class ShowMainStub:  # 行为对齐 controller.show_main 的回调入口
            def show_main(self):
                calls.append(True)

        stub = ShowMainStub()  # PySide6 弱引用槽对象，须保活
        acq.guard.activated.connect(stub.show_main)
        secondary = self.spawn(d)
        self.assertEqual(read_role(secondary), "secondary")
        deadline = time.time() + 15
        while time.time() < deadline and not calls:
            app.processEvents()
            time.sleep(0.02)
        self.assertTrue(calls, "activated signal did not fire")
        secondary.wait(timeout=10)
        acq.guard._server.close()
        acq.guard.deleteLater()
        app.processEvents()

    # ---- 干净退出后可重启 ----
    def test_clean_exit_allows_relaunch(self) -> None:
        d = self.tmpdir()
        first = self.spawn(d, life_ms=1500)
        self.assertEqual(read_role(first), "primary")
        self.assertEqual(first.wait(timeout=30), 0)
        second = self.spawn(d)
        self.assertEqual(read_role(second), "primary")

    # ---- 强制崩溃（kill）后可重启：Windows 内核自动回收命名对象 ----
    def test_forced_crash_allows_relaunch(self) -> None:
        d = self.tmpdir()
        first = self.spawn(d)
        self.assertEqual(read_role(first), "primary")
        first.kill()
        first.wait(timeout=10)
        second = self.spawn(d)
        self.assertEqual(read_role(second), "primary")

    # ---- 不同数据目录互不冲突 ----
    def test_distinct_data_dirs_independent(self) -> None:
        d1, d2 = self.tmpdir(), self.tmpdir()
        p1 = self.spawn(d1)
        self.assertEqual(read_role(p1), "primary")
        p2 = self.spawn(d2)
        self.assertEqual(read_role(p2), "primary")
        # 目录别名（尾随分隔符/大小写）指向同一 canonical 目录时仍是次实例
        alias = d2 + os.sep
        p3 = self.spawn(alias)
        self.assertEqual(read_role(p3), "secondary")

    # ---- junction/符号链接别名 -> 同一 canonical 身份（Windows） ----
    @unittest.skipUnless(sys.platform == "win32", "junction 仅 Windows")
    def test_junction_alias_shares_identity(self) -> None:
        import _winapi
        real = self.tmpdir()
        parent = tempfile.mkdtemp()
        self._tmps.append(_TmpWrap(parent))
        alias = os.path.join(parent, "junction_alias")
        _winapi.CreateJunction(real, alias)
        try:
            self.assertEqual(
                single_instance.server_name(real),
                single_instance.server_name(alias),
            )
            p1 = self.spawn(real)
            self.assertEqual(read_role(p1), "primary")
            p2 = self.spawn(alias)
            self.assertEqual(read_role(p2), "secondary")
            self.assertEqual(p2.wait(timeout=30), 0)
        finally:
            os.rmdir(alias)  # 只删除 junction 链接，不动目标目录

    # ---- 旧主在次实例重试期间退出 -> 原子接管为 primary（确定性握手） ----
    def test_handover_during_retry_window(self) -> None:
        d = Path(self.tmpdir())
        # holder 持锁但从不监听，由测试显式终止（模拟旧主在重试窗口内死亡）
        holder = self.spawn(str(d), life_ms=60000, mode="hold")
        self.assertEqual(read_role(holder), "holding")
        challenger = self.spawn(str(d), mode="handover")
        try:
            # 等待挑战者真正进入"锁已存在"重试路径（证明它先看到 AlreadyExists）
            self.assertTrue(
                wait_file(d / "retry_entered.marker", timeout=60),
                "challenger never entered the existing-owner retry path",
            )
            # 握手期间旧主仍然活着 —— 接管不是"一开始锁就空着"
            self.assertIsNone(holder.poll())
        finally:
            holder.kill()  # 此刻才释放锁，模拟旧主在重试窗口内消失
            holder.wait(timeout=10)
        # 挑战者下一轮 create 原子接管 -> primary
        self.assertEqual(read_role(challenger), "primary")
        # 且确实走过重试路径（至少一次真实 _send_activate 调用）
        self.assertGreaterEqual(int((d / "retry_count.txt").read_text()), 1)
        self.assertIsNone(challenger.poll())

    # ---- 锁被持有但无法连接 -> SECONDARY（锁证明主实例存在），绝不双开 ----
    def test_locked_unreachable_never_primary(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest.mock import patch
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
        d = self.tmpdir()
        # 真实持锁者（模拟连不上的主实例）
        holder = single_instance.acquire(d)
        self.assertEqual(holder.role, "primary")
        # 连接层失败但锁证明主实例存在 -> SECONDARY，绝不冒认 PRIMARY
        with patch.object(single_instance, "_try_connect", return_value=None):
            acq = single_instance.acquire(d)
        self.assertEqual(acq.role, "secondary")
        self.assertIsNone(acq.guard)
        holder.guard._server.close()
        holder.guard.deleteLater()
        app.processEvents()

    # ---- 持锁成功但激活通道不可用 -> FAILED（不确定不冒认） ----
    def test_listen_failure_is_failed(self) -> None:
        # Qt/Windows 允许重复 listen 同名管道（多实例），真实撞名概率极低；
        # 用边界补丁覆盖守卫的 FAILED 决策分支。
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest.mock import patch
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QLocalServer

        app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
        d = self.tmpdir()
        with patch.object(QLocalServer, "listen", return_value=False):
            acq = single_instance.acquire(d)
        self.assertEqual(acq.role, "failed")
        self.assertIsNone(acq.guard)
        app.processEvents()

    # ---- 真实 __main__.main() 集成：次实例不构造任何应用服务 ----
    @unittest.skipUnless(sys.platform == "win32", "GUI 集成测试面向 Windows 目标")
    def test_main_secondary_builds_no_services(self) -> None:
        d = self.tmpdir()
        primary = self.spawn(d, mode="gui_primary")
        self.assertEqual(read_role(primary, timeout=90), "READY")
        secondary = self.spawn(d, mode="gui_secondary")
        self.assertEqual(read_role(secondary, timeout=90), "secondary-returned")
        self.assertEqual(secondary.wait(timeout=30), 0)
        # 次实例路径绝不构造 MainWindow/Controller
        # （app.log/memo.db 由主实例正常创建，不作 pristine 断言）
        self.assertFalse((Path(d) / "win_ctor.marker").exists())
        self.assertFalse((Path(d) / "ctl_ctor.marker").exists())

    # ---- 真实 __main__.main() 集成：激活恢复隐藏/最小化的真实窗口 ----
    @unittest.skipUnless(sys.platform == "win32", "GUI 集成测试面向 Windows 目标")
    def test_main_activation_restores_hidden_and_minimized(self) -> None:
        d = Path(self.tmpdir())
        primary = self.spawn(str(d), mode="gui_primary")
        self.assertEqual(read_role(primary, timeout=90), "READY")

        for cmd in ("hide", "min"):
            (d / "cmd.txt").write_text(cmd)
            self.assertTrue(wait_file(d / "pre.json"), f"cmd {cmd} not applied")
            pre = json.loads((d / "pre.json").read_text())
            self.assertEqual(pre["cmd"], cmd, pre)
            # hide -> 不可见；min -> 仍 visible 但 minimized（Qt 语义）
            self.assertEqual(pre["visible"], cmd == "min", pre)
            self.assertEqual(pre["minimized"], cmd == "min", pre)
            (d / "pre.json").unlink()
            (d / "state.json").unlink(missing_ok=True)

            secondary = self.spawn(str(d), mode="gui_secondary")
            self.assertEqual(read_role(secondary, timeout=90), "secondary-returned")
            self.assertEqual(secondary.wait(timeout=30), 0)
            self.assertTrue(wait_file(d / "state.json"), "no activation state")
            state = json.loads((d / "state.json").read_text())
            # 真实 Controller.show_main：隐藏/最小化 -> 可见且非最小化
            self.assertTrue(state["post"]["visible"], state)
            self.assertFalse(state["post"]["minimized"], state)

        # guard 在事件循环周期内持续持有：两轮激活都送达
        self.assertTrue((d / MARKER).exists())
        self.assertIsNone(primary.poll())


class _TmpWrap:
    """让 mkdtemp 目录进入 tearDown 清理链的薄包装。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.name, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
