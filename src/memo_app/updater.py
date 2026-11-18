"""GitHub Releases updater: download verified installers, never replace live files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

from PySide6.QtCore import QObject, QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from . import theme

REPOSITORY = "pillowAIQ/lumo-notes"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases/latest"
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_METADATA = 512 * 1024
HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
         "release-assets.githubusercontent.com"}


class UpdateError(Exception):
    pass


class Cancelled(UpdateError):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise UpdateError("版本号格式无效，已停止更新")
    return tuple(map(int, value.removeprefix("v").split(".")))


def trusted_url(url):
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password
            or p.fragment or p.port not in (None, 443)):
        raise UpdateError("更新下载地址不受信任，已停止下载")
    return url


class TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str
    name: str
    size: int
    sha256: str


def parse_release(data, current=theme.APP_VERSION):
    if not isinstance(data, dict):
        raise UpdateError("更新服务器返回了无效信息")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if version_tuple(tag) <= version_tuple(current):
        return None
    version = tag.removeprefix("v")
    name = f"Lumo-{version}-windows-x64-setup.exe"
    assets = data.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("新版安装包信息无效")
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
    if len(matches) != 1:
        raise UpdateError(f"发现 {version}，但发布者尚未上传 Windows 安装包")
    asset = matches[0]
    url = asset.get("browser_download_url")
    if url != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}":
        raise UpdateError("安装包不属于轻笺的官方发布地址")
    trusted_url(url)
    digest = asset.get("digest", "")
    size = asset.get("size")
    if (not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)
            or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DOWNLOAD
            or asset.get("state", "uploaded") != "uploaded"):
        raise UpdateError("安装包缺少有效的 SHA-256 或大小信息，不能安全安装")
    return Release(version, str(data.get("body") or "暂无更新说明")[:12000],
                   url, name, size, digest[7:].lower())


def default_open(request, timeout=15):
    return urllib.request.build_opener(TrustedRedirect()).open(request, timeout=timeout)


def check_release(current=theme.APP_VERSION, opener=default_open):
    request = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"Lumo/{current}"})
    with opener(request, timeout=15) as response:
        trusted_url(response.geturl())
        data = response.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise UpdateError("更新信息过大，已停止检查")
    return parse_release(json.loads(data.decode("utf-8")), current)


def verify_installer(path, release):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size != release.size:
        raise UpdateError("安装包大小已改变，请重新下载")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != release.sha256:
        raise UpdateError("安装包校验失败，请重新下载")


def download_installer(release, cache, cancel, progress=lambda _: None, opener=default_open):
    version_tuple(release.version)
    trusted_url(release.url)
    # Release metadata has already been parsed; re-check the filename at this seam.
    if release.name != f"Lumo-{release.version}-windows-x64-setup.exe":
        raise UpdateError("无效的安装包名称")
    cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    partial = cache / f"download-{uuid.uuid4().hex}.part"
    final = cache / release.name
    try:
        if cancel.is_set():
            raise Cancelled("已取消下载")
        request = urllib.request.Request(release.url, headers={"User-Agent": f"Lumo/{theme.APP_VERSION}"})
        with opener(request, timeout=15) as response, partial.open("xb") as target:
            trusted_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != release.size:
                raise UpdateError("服务器返回的安装包大小不一致")
            total, percent = 0, -1
            while True:
                if cancel.is_set():
                    raise Cancelled("已取消下载")
                block = response.read(128 * 1024)
                if not block:
                    break
                total += len(block)
                if total > min(release.size, MAX_DOWNLOAD):
                    raise UpdateError("安装包超过预期大小")
                target.write(block)
                value = total * 100 // release.size
                if value != percent:
                    progress(value); percent = value
        if cancel.is_set():
            raise Cancelled("已取消下载")
        verify_installer(partial, release)
        os.replace(partial, final)
        return final
    finally:
        partial.unlink(missing_ok=True)


def installable_runtime():
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def launch_installer(path, release):
    if not installable_runtime():
        raise UpdateError("源码运行模式不执行安装程序，请使用 Windows 安装版")
    verify_installer(path, release)
    return subprocess.Popen([str(Path(path).resolve()), "/NORESTART"], close_fds=True)


class UpdateManager(QObject):
    changed = Signal()
    _result = Signal(object)
    _progress = Signal(int)

    def __init__(self, parent=None, *, cache=None, opener=default_open):
        super().__init__(parent)
        self.cache = Path(cache) if cache else Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation)) / "updates"
        self.opener = opener
        self.release = None
        self.path = None
        self.busy = False
        self.percent = 0
        self.status = "手动检查新版本；不会自动下载或安装。"
        self._cancel = threading.Event()
        self._closed = False
        self._result.connect(self._accept)
        self._progress.connect(self._set_progress)

    def _set_progress(self, value):
        self.percent = value; self.changed.emit()

    def _start(self, kind, operation):
        if self.busy or self._closed:
            return
        self.busy = True; self.percent = 0; self._cancel.clear()
        self.status = "正在检查更新…" if kind == "check" else "正在下载安装包…"
        self.changed.emit()

        def run():
            try:
                value = operation()
                result = (kind, value, None)
            except Exception as error:
                result = (kind, None, str(error))
            if not self._closed:
                try:
                    self._result.emit(result)
                except RuntimeError:
                    pass  # QApplication may already have shut down.
        threading.Thread(target=run, name="Lumo-update", daemon=True).start()

    def _accept(self, result):
        if self._closed:
            return
        self.busy = False
        kind, value, error = result
        if self._cancel.is_set():
            self.status = "已取消更新操作。"
            self.changed.emit()
            return
        if error:
            self.status = f"更新未完成：{error}"
        elif kind == "check":
            self.release, self.path = value, None
            self.status = f"发现新版 {value.version}" if value else "当前已是最新版本。"
        else:
            self.path = value
            self.status = "下载完成，安装包已校验，可以安装。"
        self.changed.emit()

    def check(self):
        self._start("check", lambda: check_release(theme.APP_VERSION, self.opener))

    def download(self):
        if self.release:
            release = self.release
            self._start("download", lambda: download_installer(release, self.cache, self._cancel,
                        self._progress.emit, self.opener))

    def cancel(self):
        self._cancel.set()

    def install(self):
        if self.busy or not self.path or not self.release:
            raise UpdateError("请先下载并校验安装包")
        launch_installer(self.path, self.release)
        QApplication.instance().quit()

    def shutdown(self):
        self._closed = True; self.cancel()


def manager():
    app = QApplication.instance()
    if not hasattr(app, "_lumo_update_manager"):
        app._lumo_update_manager = UpdateManager(app)
        app.aboutToQuit.connect(app._lumo_update_manager.shutdown)
    return app._lumo_update_manager


class UpdatePanel(QWidget):
    def __init__(self):
        super().__init__()
        self.service = manager()
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10); root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.version = QLabel(f"当前版本 {theme.APP_VERSION}")
        self.status = QLabel(); self.status.setWordWrap(True)
        self.notes = QTextEdit(); self.notes.setReadOnly(True); self.notes.setMaximumHeight(150)
        self.progress = QProgressBar()
        self.check_button = QPushButton("检查更新")
        self.download_button = QPushButton("下载新版安装包")
        self.cancel_button = QPushButton("取消下载")
        self.install_button = QPushButton("安装更新并退出轻笺")
        for widget in (self.version, self.status, self.notes, self.progress, self.check_button,
                       self.download_button, self.cancel_button, self.install_button):
            root.addWidget(widget)
        self.check_button.clicked.connect(self.service.check)
        self.download_button.clicked.connect(self.service.download)
        self.cancel_button.clicked.connect(self.service.cancel)
        self.install_button.clicked.connect(self.install)
        self.service.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        s = self.service
        self.status.setText(s.status)
        self.check_button.setEnabled(not s.busy)
        self.notes.setVisible(s.release is not None)
        self.notes.setPlainText(s.release.notes if s.release else "")
        self.progress.setVisible(s.busy); self.progress.setValue(s.percent)
        self.download_button.setVisible(bool(s.release and not s.path))
        self.download_button.setEnabled(not s.busy)
        self.cancel_button.setVisible(s.busy)
        self.install_button.setVisible(s.path is not None)
        self.install_button.setEnabled(not s.busy and installable_runtime())
        self.install_button.setToolTip("" if installable_runtime() else "源码模式不执行安装程序")

    def install(self):
        if QMessageBox.question(self, "安装轻笺更新", "轻笺将退出并打开新版安装向导。备忘录和设置会保留。继续吗？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.install()
        except Exception as error:
            self.service.status = str(error); self.refresh()
