import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import memo_app
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from memo_app import updater as u

app = QApplication.instance() or QApplication([])


def metadata(version="0.4.0", payload=b"installer"):
    name = f"Lumo-{version}-windows-x64-setup.exe"
    return {"tag_name": f"v{version}", "body": "<b>plain release text</b>", "assets": [{
        "name": name, "size": len(payload), "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "browser_download_url": f"https://github.com/{u.REPOSITORY}/releases/download/v{version}/{name}"}]}


class Response(io.BytesIO):
    def __init__(self, data, url, length=None):
        super().__init__(data); self.url = url
        self.headers = {} if length is None else {"Content-Length": str(length)}
    def geturl(self):
        return self.url


class UpdateTests(unittest.TestCase):
    def test_versions_and_stable_policy(self):
        self.assertGreater(u.version_tuple("0.10.0"), u.version_tuple("0.9.0"))
        self.assertIsNone(u.parse_release(metadata("0.3.0"), "0.3.0"))
        self.assertIsNone(u.parse_release(metadata("0.2.0"), "0.3.0"))
        m = metadata(); m["prerelease"] = True
        self.assertIsNone(u.parse_release(m))
        for bad in ("v0.4.0-beta", "../../x", None, "0.04.0"):
            with self.assertRaises(u.UpdateError): u.version_tuple(bad)

    def test_reject_untrusted_metadata_and_missing_assets(self):
        for field, value in (("digest", ""), ("size", -1), ("size", True),
                             ("browser_download_url", "https://evil.test/setup.exe")):
            m = metadata(); m["assets"][0][field] = value
            with self.assertRaises(u.UpdateError): u.parse_release(m)
        m = metadata(); m["assets"] = []
        with self.assertRaisesRegex(u.UpdateError, "尚未上传"): u.parse_release(m)

    def test_reject_redirects_and_http(self):
        for url in ("http://github.com/x", "https://github.com.evil.test/x",
                    "https://user@github.com/x", "https://github.com:444/x"):
            with self.assertRaises(u.UpdateError): u.trusted_url(url)
        with self.assertRaises(u.UpdateError):
            u.TrustedRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test/x")

    def test_download_and_reverify(self):
        payload = b"installer"
        release = u.parse_release(metadata(payload=payload))
        with tempfile.TemporaryDirectory() as temp:
            opener = lambda req, **kw: Response(payload, release.url, len(payload))
            path = u.download_installer(release, temp, threading.Event(), opener=opener)
            self.assertEqual(path.read_bytes(), payload)
            path.write_bytes(b"tampered!")
            with patch.object(u, "installable_runtime", return_value=True), patch.object(u.subprocess, "Popen") as launch:
                with self.assertRaises(u.UpdateError): u.launch_installer(path, release)
                launch.assert_not_called()

    def test_failures_and_cancellation_remove_partials(self):
        release = u.parse_release(metadata())
        with tempfile.TemporaryDirectory() as temp:
            sentinel = Path(temp) / "keep.txt"; sentinel.write_text("keep")
            for payload in (b"wronghash", b"short", b"x" * 100):
                with self.assertRaises(u.UpdateError):
                    u.download_installer(release, temp, threading.Event(),
                        opener=lambda req, **kw: Response(payload, release.url))
                self.assertEqual(list(Path(temp).glob("*.part")), [])
            event = threading.Event(); event.set()
            with self.assertRaises(u.Cancelled): u.download_installer(release, temp, event)
            self.assertEqual(sentinel.read_text(), "keep")

    def test_source_cannot_launch_installer(self):
        with patch.object(u, "installable_runtime", return_value=False), patch.object(u.subprocess, "Popen") as launch:
            with self.assertRaises(u.UpdateError): u.launch_installer(Path("anything.exe"), u.parse_release(metadata()))
            launch.assert_not_called()

    def test_close_panel_during_check(self):
        gate = threading.Event()
        def opener(request, **kw):
            gate.wait(2)
            return Response(json.dumps(metadata()).encode(), u.API_URL)
        with tempfile.TemporaryDirectory() as temp:
            service = u.UpdateManager(app, cache=temp, opener=opener)
            with patch.object(u, "manager", return_value=service):
                panel = u.UpdatePanel(); panel.show(); service.check()
                panel.close(); panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                gate.set()
                for _ in range(100):
                    QTest.qWait(10)
                    if not service.busy: break
                self.assertFalse(service.busy)
                self.assertEqual(service.release.version, "0.4.0")
            service.shutdown(); service.deleteLater()


if __name__ == "__main__": unittest.main()
