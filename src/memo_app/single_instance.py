"""单实例守卫：同一规范数据目录下最多一个主进程。

仅使用 Qt 内置本地 IPC/锁，无新依赖：
- QSharedMemory 作为所有权锁（Windows 上为内核命名对象，create() 原子，
  进程被强杀后由系统自动回收，无残留锁）。
- QLocalServer / QLocalSocket（Windows 命名管道）仅作激活通道：
  次实例连接并发送 ACTIVATE，主实例收到后发 activated 信号。

注意：Qt/Windows 允许重复 listen 同一管道名，listen 本身不构成互斥，
因此必须先持锁再监听。

判定顺序：
1. shm.create() 成功 -> 本进程持锁，尝试 listen：成功即 PRIMARY；
   listen 失败说明名字被无主监听者占用 -> FAILED（绝不冒认主实例）。
2. create() 失败 -> 锁被持有，主实例必然存在：重试连接并发送
   ACTIVATE（覆盖主实例持锁后尚未 listen 完成的毫秒级窗口），
   随后以 SECONDARY 退出，不创建任何应用服务、不修改数据。
3. 既无法持锁也无法确认/送达 -> FAILED（宁可退出也绝不双开）。

POSIX 兜底：硬崩溃会残留共享内存段，连接不上时 attach/detach 回收
陈旧段后重试一次 create。Windows 不存在此问题（内核自动回收）。
源码运行与打包（frozen）同由 MEMO_APP_DATA_DIR / ~/.memo_app 推导
数据目录，共享同一实例身份；不同数据目录哈希不同，互不干扰。
"""
from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSharedMemory, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import db

PRIMARY = "primary"
SECONDARY = "secondary"
FAILED = "failed"

_ACTIVATE = b"ACTIVATE\n"
_CONNECT_MS = 400
_WRITE_MS = 400
_RETRIES = 4
_RETRY_GAP_MS = 150


def server_name(data_dir: Path | str | None = None) -> str:
    """数据目录 -> 本地 IPC/锁名。规范化后哈希，同一物理目录同名。

    Path.resolve() 解析符号链接/ junction /8.3 短名等别名；目录尚不存在
    时按既有祖先解析（strict=False），保证首启与后续启动键一致。
    """
    d = Path(data_dir) if data_dir is not None else db.data_dir()
    canon = os.path.normcase(str(d.expanduser().resolve()))
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]
    return f"memo-app-{digest}"


def _try_connect(name: str, timeout_ms: int = _CONNECT_MS) -> QLocalSocket | None:
    """尝试连上既有主实例；成功返回已连接 socket，失败返回 None。"""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(timeout_ms):
        return sock
    sock.abort()
    return None


def _send_activate(name: str, timeout_ms: int = _CONNECT_MS) -> bool:
    """向既有主实例请求激活。连上通道即返回 True（尽力送达）。"""
    sock = _try_connect(name, timeout_ms)
    if sock is None:
        return False
    try:
        sock.write(_ACTIVATE)
        sock.flush()
        sock.waitForBytesWritten(_WRITE_MS)
    finally:
        sock.disconnectFromServer()
    return True


class InstanceGuard(QObject):
    """主实例持有的锁 + 服务端口；收到任何连接即发出 activated 信号。

    须保持引用直到进程退出：QSharedMemory 析构即释放所有权锁。
    """

    activated = Signal()

    def __init__(
        self,
        shm: QSharedMemory,
        server: QLocalServer,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._shm = shm
        self._server = server
        server.newConnection.connect(self._on_new_connection)

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is not None:
                sock.disconnectFromServer()
                sock.deleteLater()
        self.activated.emit()


@dataclass
class Acquisition:
    role: str                            # PRIMARY / SECONDARY / FAILED
    guard: InstanceGuard | None = None   # 仅 PRIMARY 时非空，须保持引用


def _primary(name: str, shm: QSharedMemory, parent: QObject | None) -> Acquisition:
    """已持锁：建立激活通道，成功即主实例。"""
    server = QLocalServer(parent)
    if server.listen(name):
        return Acquisition(PRIMARY, InstanceGuard(shm, server, parent))
    # 锁在我手上但管道名不可用：无主监听者，状态不确定 -> FAILED
    server.close()
    shm.detach()
    return Acquisition(FAILED)


def acquire(
    data_dir: Path | str | None = None,
    *,
    timeout_ms: int = _CONNECT_MS,
    retries: int = _RETRIES,
    retry_gap_ms: int = _RETRY_GAP_MS,
    parent: QObject | None = None,
) -> Acquisition:
    """建立单实例所有权。须在创建 MainWindow/Controller/托盘/热键/同步之前调用。"""
    name = server_name(data_dir)
    shm = QSharedMemory(name, parent)

    # 1) 原子持锁：并发首启只有一个进程能 create 成功
    if shm.create(1):
        return _primary(name, shm, parent)
    # create 失败但非"已存在"（如资源耗尽）：状态不确定，不冒认次实例
    if shm.error() != QSharedMemory.SharedMemoryError.AlreadyExists:
        return Acquisition(FAILED)

    # 2) 锁被持有 -> 主实例存在，重试连接发送激活请求。
    #    重试覆盖主实例已持锁但尚未 listen 完成的启动窗口；
    #    每轮同时原子重试持锁：若旧主恰在重试期间退出则接管，
    #    避免"激活未送达且无人持锁"的静默退出。
    for i in range(max(1, retries)):
        if _send_activate(name, timeout_ms):
            return Acquisition(SECONDARY)
        if shm.create(1):
            return _primary(name, shm, parent)
        if shm.error() != QSharedMemory.SharedMemoryError.AlreadyExists:
            return Acquisition(FAILED)
        if i + 1 < retries:
            QThread.msleep(retry_gap_ms)

    # 3) POSIX：硬崩溃残留段 -> 回收后重试持锁；Windows 不会残留
    if sys.platform != "win32" and shm.attach():
        shm.detach()
        if shm.create(1):
            return _primary(name, shm, parent)
        # 仍无法持锁：状态不确定
        return Acquisition(FAILED)

    # Windows 走到这里 = 锁被持有但管道始终连不上：
    # 主实例必然存在（锁证明），只是激活未能送达 -> 以 SECONDARY 退出，
    # 绝不冒认主实例造成双开。
    return Acquisition(SECONDARY)
