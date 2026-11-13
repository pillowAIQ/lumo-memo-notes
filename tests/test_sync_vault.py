"""vault_setup + service.run_job 端到端测试（假 WebDAV，无 Qt）。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sync_transport import FakeWebDAV  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

from memo_app import db  # noqa: E402
from memo_app.sync import crypto, service, store  # noqa: E402
from memo_app.sync.transport import WebDAVClient  # noqa: E402
from memo_app.sync import vault_setup  # noqa: E402

VID = "12345678-1234-1234-1234-123456789abc"
VAULT_PW = "test-vault-password-123"


class VaultSetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeWebDAV.store = {}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeWebDAV)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        FakeWebDAV.store = {}
        os.environ.pop("MEMO_APP_DATA_DIR", None)

    def tearDown(self):
        os.environ.pop("MEMO_APP_DATA_DIR", None)

    def _mk(self, tmp: str) -> None:
        os.environ["MEMO_APP_DATA_DIR"] = tmp
        db.init_db()

    def _client(self, vid: str | None = VID) -> WebDAVClient:
        return WebDAVClient(
            f"http://127.0.0.1:{self.port}/dav/", "u", "p",
            vault_id=vid, allow_http=True, timeout=5,
        )

    def test_create_and_open_vault(self) -> None:
        tmp = tempfile.mkdtemp()
        self._mk(tmp)
        with db.db() as conn:
            wid = store.profile_writer_id(conn)
        c = self._client()
        mk = vault_setup.create_vault(c, VID, wid, VAULT_PW)
        # vault.json 已上传
        self.assertTrue(
            f"/dav/memo-sync/{VID}/vault.json" in FakeWebDAV.store
        )
        mk2, _cfg = vault_setup.open_remote_vault(c, VAULT_PW)
        self.assertEqual(mk, mk2)
        # 错误密码必须失败
        with self.assertRaises(Exception):
            vault_setup.open_remote_vault(c, "wrong-password")

    def test_create_vault_idempotent_guard(self) -> None:
        """同 vault_id 重复 create 必须失败（不覆盖 vault.json）。"""
        tmp = tempfile.mkdtemp()
        self._mk(tmp)
        with db.db() as conn:
            wid = store.profile_writer_id(conn)
        c = self._client()
        vault_setup.create_vault(c, VID, wid, VAULT_PW)
        with self.assertRaises(Exception):
            vault_setup.create_vault(c, VID, wid, "another-pw")

    def test_run_job_full_flow_two_devices(self) -> None:
        """run_job 全链路：A 建库->写->同步；B 加入->同步->拿到数据。"""
        da, db_ = tempfile.mkdtemp(), tempfile.mkdtemp()
        base = f"http://127.0.0.1:{self.port}/dav/"

        # A：建库
        self._mk(da)
        r = service.run_job({
            "kind": "setup_create", "base_url": base,
            "account": "u", "app_password": "p",
            "vault_password": VAULT_PW, "vault_id": VID, "allow_http": True,
        })
        self.assertTrue(r["ok"], r)
        os.environ["MEMO_APP_DATA_DIR"] = da
        db.add_memo("服务层测试数据", cat_name="服务分类")
        r = service.run_job({
            "kind": "sync", "base_url": base,
            "account": "u", "app_password": "p",
            "vault_password": VAULT_PW, "allow_http": True,
        })
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(r["stats"]["published"], 1)

        # B：加入同一库
        self._mk(db_)
        r = service.run_job({
            "kind": "setup_join", "base_url": base,
            "account": "u", "app_password": "p",
            "vault_password": VAULT_PW, "vault_id": VID, "allow_http": True,
        })
        self.assertTrue(r["ok"], r)
        r = service.run_job({
            "kind": "sync", "base_url": base,
            "account": "u", "app_password": "p",
            "vault_password": VAULT_PW, "allow_http": True,
        })
        self.assertTrue(r["ok"], r)
        os.environ["MEMO_APP_DATA_DIR"] = db_
        bm = db.list_memos()
        self.assertEqual(len(bm), 1)
        self.assertEqual(bm[0].content, "服务层测试数据")
        self.assertTrue(any(c.name == "服务分类" for c in db.list_categories()))

    def test_run_job_wrong_vault_password(self) -> None:
        da = tempfile.mkdtemp()
        self._mk(da)
        base = f"http://127.0.0.1:{self.port}/dav/"
        service.run_job({
            "kind": "setup_create", "base_url": base,
            "account": "u", "app_password": "p",
            "vault_password": VAULT_PW, "vault_id": VID, "allow_http": True,
        })
        # 同库错密码 join 必须失败（run_job 直接调用会抛异常，
        # 生产路径由 _Worker._run 捕获转成错误结果）
        db2 = tempfile.mkdtemp()
        self._mk(db2)
        with self.assertRaises(Exception):
            service.run_job({
                "kind": "setup_join", "base_url": base,
                "account": "u", "app_password": "p",
                "vault_password": "wrong-Password-1",
                "vault_id": VID, "allow_http": True,
            })


if __name__ == "__main__":
    unittest.main()
