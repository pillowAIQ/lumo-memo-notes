"""图片同步：A 加图 -> 同步 -> B 拿到同名文件；删除、旧客户端修订、远端缺失、篡改。

复用 test_sync_engine 的双设备 + 假 WebDAV 夹具。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import threading  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

import test_sync_engine as _eng_fixture  # noqa: E402
from test_sync_transport import FakeWebDAV  # noqa: E402

MK, VID = _eng_fixture.MK, _eng_fixture.VID

from memo_app import db  # noqa: E402
from memo_app.sync import crypto, engine, protocol, store  # noqa: E402


def _use(dev) -> None:
    os.environ["MEMO_APP_DATA_DIR"] = dev.dir.name


def _add_image(memo_id: int, payload: bytes) -> str:
    name = db.store_image_bytes(payload, "png")
    db.add_memo_image(memo_id, name)
    return name


def _remote_blob_keys() -> list[str]:
    return [k for k in FakeWebDAV.store if "/blobs/" in k]


class ImageSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeWebDAV.store = {}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeWebDAV)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        FakeWebDAV.store = {}
        os.environ.pop("MEMO_APP_DATA_DIR", None)
        self._retry = engine.BLOB_MISS_RETRY

    def tearDown(self):
        engine.BLOB_MISS_RETRY = self._retry
        os.environ.pop("MEMO_APP_DATA_DIR", None)

    def _dev(self, name: str):
        return _eng_fixture._Device(self.port, name)

    def _uuid_of(self, mid: int) -> str:
        return db.get_memo(mid).uuid

    def _memo_by_uuid(self, uuid: str):
        return next(m for m in db.list_memos() if m.uuid == uuid)

    def test_image_round_trip_and_delete(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        _use(a)
        mid = db.add_memo("带图")
        uuid = self._uuid_of(mid)
        n1 = _add_image(mid, b"\x89PNG-first-image")
        n2 = _add_image(mid, b"\x89PNG-second-image")
        stats = a.sync()
        self.assertEqual(stats["blobs_up"], 2)
        self.assertEqual(len(_remote_blob_keys()), 2)
        # 远端是密文，不含明文内容
        for k in _remote_blob_keys():
            self.assertNotIn(b"first-image", FakeWebDAV.store[k])

        stats = b.sync()
        self.assertEqual(stats["blobs_down"], 2)
        _use(b)
        bm = self._memo_by_uuid(uuid)
        self.assertEqual([f for _, f in db.list_memo_images(bm.id)], [n1, n2])
        self.assertEqual(db.image_path(n1).read_bytes(), b"\x89PNG-first-image")
        # B 已有的图片不再重复上传
        self.assertEqual(b.sync()["blobs_up"], 0)

        # A 删除第一张 -> B 同步后只剩第二张
        _use(a)
        first_id = db.list_memo_images(mid)[0][0]
        db.delete_memo_image(first_id)
        a.sync()
        b.sync()
        _use(b)
        self.assertEqual([f for _, f in db.list_memo_images(bm.id)], [n2])

        # B 再加一张 -> A 收到
        _use(b)
        n3 = _add_image(bm.id, b"\x89PNG-from-B")
        b.sync()
        a.sync()
        _use(a)
        self.assertEqual([f for _, f in db.list_memo_images(mid)], [n2, n3])
        self.assertTrue(db.image_path(n3).exists())

    def test_old_client_revision_keeps_images(self) -> None:
        """旧版本客户端的修订不带 images 字段：不能清空接收端的图片。"""
        a, b = self._dev("A"), self._dev("B")
        _use(a)
        mid = db.add_memo("原文")
        uuid = self._uuid_of(mid)
        name = _add_image(mid, b"\x89PNG-keep-me")
        a.sync()
        b.sync()
        # 模拟旧客户端 B 改了正文：修订里没有 images
        _use(b)
        bm = self._memo_by_uuid(uuid)
        with db.db() as conn:
            row = conn.execute("SELECT * FROM memos WHERE id = ?", (bm.id,)).fetchone()
            store.enqueue(conn, entity="memo", entity_uuid=uuid, op="upsert", payload={
                "content": "旧客户端改过", "is_pinned": False, "is_done": False,
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "deleted_at": None, "due_ts": None, "priority": 0, "recur": None,
                "remind_advance": 0, "category_uuids": [],
            })
        b.sync()
        a.sync()
        _use(a)
        self.assertEqual(db.get_memo(mid).content, "旧客户端改过")
        self.assertEqual([f for _, f in db.list_memo_images(mid)], [name])

    def test_missing_blob_retried_later(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        _use(a)
        mid = db.add_memo("图还没传完")
        name = _add_image(mid, b"\x89PNG-late")
        a.sync()
        # 模拟 A 的图片上传丢失：删掉远端 blob
        for k in _remote_blob_keys():
            del FakeWebDAV.store[k]
        stats = b.sync()
        self.assertEqual(stats["blobs_down"], 0)
        _use(b)
        self.assertFalse(db.image_path(name).exists())
        # 重试窗口内不会反复请求
        self.assertEqual(b.sync()["blobs_down"], 0)
        # A 重新上传后，过了重试窗口 B 能拿到
        _use(a)
        with db.db() as conn:
            conn.execute("UPDATE sync_blobs SET uploaded = 0")
        a.sync()
        engine.BLOB_MISS_RETRY = 0
        self.assertEqual(b.sync()["blobs_down"], 1)
        _use(b)
        self.assertTrue(db.image_path(name).exists())

    def test_tampered_blob_rejected(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        _use(a)
        mid = db.add_memo("被篡改")
        name = _add_image(mid, b"\x89PNG-genuine")
        a.sync()
        key = _remote_blob_keys()[0]
        # 用正确密钥与 AAD 加密了别的内容：哈希对不上，必须拒收
        FakeWebDAV.store[key] = crypto.aes_gcm_encrypt(
            MK, b"\x89PNG-evil", crypto.blob_aad(VID, name)
        )
        self.assertEqual(b.sync()["blobs_down"], 0)
        _use(b)
        self.assertFalse(db.image_path(name).exists())
        # 换名重放（AAD 绑定文件名）同样解不开
        other = hashlib.sha256(b"x").hexdigest() + ".png"
        FakeWebDAV.store[key] = crypto.aes_gcm_encrypt(MK, b"\x89PNG-genuine", crypto.blob_aad(VID, other))
        engine.BLOB_MISS_RETRY = 0
        self.assertEqual(b.sync()["blobs_down"], 0)

    def test_invalid_image_names_rejected(self) -> None:
        """远端修订里的图片名必须是内容哈希：路径穿越等直接判坏 op。"""
        eng_check = engine.Engine._check_memo_data
        base = {"content": "x", "is_pinned": False, "is_done": False,
                "created_at": "", "updated_at": ""}
        eng_check(dict(base, images=[hashlib.sha256(b"a").hexdigest() + ".jpg"]))
        for bad in (["../../evil.png"], ["abc.png"], "notalist", [1]):
            with self.assertRaises(ValueError):
                eng_check(dict(base, images=bad))
        with self.assertRaises(protocol.ProtocolError):
            protocol.blob_rel("../x.png")

    def test_legacy_image_names_migrated_and_enqueued(self) -> None:
        a = self._dev("A")
        _use(a)
        mid = db.add_memo("旧版图片")
        folder = db.images_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "0123abcd.png").write_bytes(b"\x89PNG-legacy")
        with db.db() as conn:
            conn.execute(
                "INSERT INTO memo_images(memo_id, filename, created_at) VALUES (?, ?, '')",
                (mid, "0123abcd.png"),
            )
        db.init_db()  # 下次启动时迁移
        names = [f for _, f in db.list_memo_images(mid)]
        self.assertEqual(names, [hashlib.sha256(b"\x89PNG-legacy").hexdigest() + ".png"])
        self.assertTrue(db.image_path(names[0]).exists())
        self.assertFalse((folder / "0123abcd.png").exists())
        with db.db() as conn:
            payloads = [json.loads(r["payload"]) for r in conn.execute(
                "SELECT payload FROM sync_outbox WHERE entity = 'memo' ORDER BY seq")]
        self.assertEqual(payloads[-1]["data"]["images"], names)


if __name__ == "__main__":
    unittest.main()
