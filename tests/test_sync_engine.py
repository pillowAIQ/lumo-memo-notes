"""engine.py 双客户端闭环测试：A 写 -> 同步 -> B 拉取 -> 双向收敛。

用两个独立 MEMO_APP_DATA_DIR 模拟两台设备，共享假 WebDAV 服务与主密钥。
运行：python -m unittest discover -s tests
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sync_transport import FakeWebDAV  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
import threading  # noqa: E402

from memo_app import db  # noqa: E402
from memo_app.sync import crypto, engine, store, transport  # noqa: E402

VID = "12345678-1234-1234-1234-123456789abc"
MK = crypto.generate_master_key()


class _Device:
    """一台模拟设备：独立数据目录 + 独立 writer + 独立 engine。"""

    def __init__(self, port: int, name: str, conflict_mode: str = "lww"):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["MEMO_APP_DATA_DIR"] = self.dir.name
        db.init_db()
        self.client = transport.WebDAVClient(
            f"http://127.0.0.1:{port}/dav/", "u", "p",
            vault_id=VID, allow_http=True, timeout=5,
        )
        self.eng = engine.Engine(self.client, MK, VID, conflict_mode)
        self.name = name

    def sync(self):
        os.environ["MEMO_APP_DATA_DIR"] = self.dir.name
        return self.eng.sync_once()

    def memos(self):
        os.environ["MEMO_APP_DATA_DIR"] = self.dir.name
        return db.list_memos()

    def memos_trash(self):
        os.environ["MEMO_APP_DATA_DIR"] = self.dir.name
        return db.list_memos(trash=True)

    def categories(self):
        os.environ["MEMO_APP_DATA_DIR"] = self.dir.name
        return db.list_categories()


class EngineTest(unittest.TestCase):
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

    def _dev(self, name: str) -> _Device:
        return _Device(self.port, name)

    def test_a_to_b_basic_sync(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        # A 新增分类+备忘录
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("A的备忘录", cat_name="工作")
        a.sync()  # A 发布
        b.sync()  # B 拉取
        bm = b.memos()
        self.assertEqual(len(bm), 1)
        self.assertEqual(bm[0].content, "A的备忘录")
        self.assertNotEqual(bm[0].uuid, "")
        # 分类也同步了
        self.assertTrue(any(c.name == "工作" for c in b.categories()))
        # 归属正确：memo 的 category_uuids 解析到 B 本地分类
        bmap = db.memo_category_map()
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        bmap = db.memo_category_map()
        self.assertEqual(len(bmap.get(bm[0].id, set())), 1)

    def test_bidirectional_converge(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.add_memo("来自A")
        a.sync()
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        db.add_memo("来自B")
        b.sync()   # B 发布自己的 + 拉 A 的
        a.sync()   # A 拉 B 的
        a_contents = sorted(m.content for m in a.memos())
        b_contents = sorted(m.content for m in b.memos())
        self.assertEqual(a_contents, ["来自A", "来自B"])
        self.assertEqual(b_contents, ["来自A", "来自B"])

    def test_lww_same_entity_converge(self) -> None:
        """两端离线改同一条：最终两端内容一致（LWW 确定性收敛）。"""
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("原始")
        uuid = db.list_memos()[0].uuid
        a.sync()
        b.sync()  # B 拿到同一 uuid 的本地副本
        # 双方离线各自修改（不先同步）
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.update_content(mid, "A的修改")
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        bmid = next(m.id for m in b.memos() if m.uuid == uuid)
        db.update_content(bmid, "B的修改")
        a.sync(); b.sync()  # 互相拉取对方的修改
        a.sync(); b.sync()  # 再拉一轮确保收敛
        ac = next(m.content for m in a.memos() if m.uuid == uuid)
        bc = next(m.content for m in b.memos() if m.uuid == uuid)
        self.assertEqual(ac, bc)  # LWW：两端一致，不静默分叉
        self.assertIn(ac, ("A的修改", "B的修改"))

    def test_delete_propagates(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("将被删除")
        a.sync(); b.sync()
        self.assertEqual(len(b.memos()), 1)
        # A 彻底删除
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.purge(mid)
        a.sync(); b.sync()
        self.assertEqual(len(b.memos()), 0)
        self.assertEqual(len(b.memos_trash()), 0)

    def test_soft_delete_propagates_to_trash(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("进回收站")
        a.sync(); b.sync()
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.soft_delete(mid)
        a.sync(); b.sync()
        self.assertEqual(len(b.memos()), 0)
        self.assertEqual(len(b.memos_trash()), 1)

    def test_idempotent_repull(self) -> None:
        """重复拉取不产生重复记录。"""
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.add_memo("幂等测试")
        a.sync()
        b.sync(); b.sync(); b.sync()
        self.assertEqual(len(b.memos()), 1)

    def test_priority_and_task_props_sync(self) -> None:
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("任务", due_ts=1893456000, priority=3, recur="daily")
        a.sync(); b.sync()
        bm = b.memos()[0]
        self.assertEqual(bm.priority, 3)
        self.assertEqual(bm.due_ts, 1893456000)
        self.assertEqual(bm.recur, "daily")


    def test_bootstrap_upgraded_db(self) -> None:
        """升级前数据（无 outbox 记录）首次同步必须上传。"""
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.add_memo("升级前的旧数据", cat_name="旧分类")
        # 模拟 M1 之前的库：清掉 outbox（实体 state 基线仍在）
        with db.db() as conn:
            conn.execute("DELETE FROM sync_outbox")
        a.sync()
        b.sync()
        bm = b.memos()
        self.assertEqual(len(bm), 1)
        self.assertEqual(bm[0].content, "升级前的旧数据")
        self.assertTrue(any(c.name == "旧分类" for c in b.categories()))

    def test_no_duplicate_ops_on_first_sync(self) -> None:
        """正常写入产生的 outbox 不应被 bootstrap 重复入队。"""
        a, _b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.add_memo("正常数据")
        with db.db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) c FROM sync_outbox WHERE sent = 0"
            ).fetchone()["c"]
        a.sync()
        with db.db() as conn:
            # bootstrap 不得新增任何 outbox 行：已有 pending 的实体
            # 由正常队列上传，总行数应与同步前完全一致
            outbox_rows = conn.execute(
                "SELECT COUNT(*) c FROM sync_outbox"
            ).fetchone()["c"]
        self.assertEqual(outbox_rows, before)

    def test_unpublished_batch_republished(self) -> None:
        """崩溃恢复：published=0 的批次下轮补发，head 只发一次最大 seq。"""
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.add_memo("崩溃恢复测试")
        a.sync()
        # 人为制造"commit 已上传但 head 未发布"的崩溃现场
        with db.db() as conn:
            conn.execute("UPDATE sync_batches SET published = 0")
            conn.execute("UPDATE sync_outbox SET sent = 1")
        stats = a.sync()
        self.assertGreaterEqual(stats["published"], 1)
        b.sync()
        self.assertEqual(len(b.memos()), 1)

    def test_bad_op_does_not_stall_log(self) -> None:
        """单条畸形 op 被跳过（bad_ops+1），不阻塞同批其他 op。"""
        a, _b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.add_memo("好数据")
        a.sync()
        os.environ["MEMO_APP_DATA_DIR"] = _b.dir.name
        eng = _b.eng
        bad_batch = {
            "v": 1, "batch_id": "12345678-1234-1234-1234-123456789abc",
            "writer_id": "87654321-4321-4321-4321-abcdefabcdef",
            "seq": 99, "created_utc": "2025-01-01T00:00:00+00:00",
            "ops": [
                # 畸形：data 缺 content -> 应被跳过而非抛异常
                {"v": 1, "entity": "memo",
                 "entity_uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                 "op": "upsert", "rev_ts": 1.0, "rev_seq": 1,
                 "writer_id": "87654321-4321-4321-4321-abcdefabcdef",
                 "data": {"is_pinned": True}},
                # 正常 delete op
                {"v": 1, "entity": "memo",
                 "entity_uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                 "op": "delete", "rev_ts": 2.0, "rev_seq": 2,
                 "writer_id": "87654321-4321-4321-4321-abcdefabcdef",
                 "data": {}},
            ],
        }
        applied, bad = eng._apply_batch(
            bad_batch, bad_batch["writer_id"], bad_batch["seq"],
            bad_batch["seq"],
        )
        self.assertEqual(bad, 1)
        self.assertEqual(applied, 1)  # delete op 正常应用

    def test_pending_link_resolved_when_category_arrives(self) -> None:
        """memo 先于其分类到达：归属挂起，分类到达后回填。"""
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("跨writer依赖", cat_name="迟到分类")
        a.sync()
        # B 端：先只应用 memo op（模拟分类 writer 尚未拉取）
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        eng = b.eng
        # 在 B 上手工执行 _apply_op 序列
        memo_op = {
            "v": 1, "entity": "memo",
            "entity_uuid": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "op": "upsert", "rev_ts": 100.0, "rev_seq": 1,
            "writer_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "data": {
                "content": "等分类的备忘", "is_pinned": False,
                "is_done": False, "created_at": "2025-01-01",
                "updated_at": "2025-01-01", "deleted_at": None,
                "due_ts": None, "priority": 0, "recur": None,
                "remind_advance": 0,
                "category_uuids": ["eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"],
            },
        }
        cat_op = {
            "v": 1, "entity": "category",
            "entity_uuid": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
            "op": "upsert", "rev_ts": 101.0, "rev_seq": 2,
            "writer_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "data": {"name": "迟到分类", "created_at": "2025-01-01"},
        }
        with db.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            eng._apply_op(conn, memo_op)
            # 此时分类未到：memo 无归属，pending_links 应有记录
            mid_row = conn.execute(
                "SELECT id FROM memos WHERE uuid = ?",
                ("cccccccc-cccc-cccc-cccc-cccccccccccc",),
            ).fetchone()
            links = conn.execute(
                "SELECT COUNT(*) c FROM memo_categories WHERE memo_id = ?",
                (mid_row["id"],),
            ).fetchone()["c"]
            self.assertEqual(links, 0)
            pend = conn.execute(
                "SELECT COUNT(*) c FROM sync_pending_links"
            ).fetchone()["c"]
            self.assertEqual(pend, 1)
            # 分类到达 -> 回填
            eng._apply_op(conn, cat_op)
            links = conn.execute(
                "SELECT COUNT(*) c FROM memo_categories WHERE memo_id = ?",
                (mid_row["id"],),
            ).fetchone()["c"]
            self.assertEqual(links, 1)
            pend = conn.execute(
                "SELECT COUNT(*) c FROM sync_pending_links"
            ).fetchone()["c"]
            self.assertEqual(pend, 0)


    def test_clock_skew_still_converges(self) -> None:
        """SYNC-001：对端时钟偏快导致 rev_ts 虚高时，本机后续编辑
        必须被逻辑抬升，顺序修改仍能收敛。"""
        a, b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("原始")
        a.sync(); b.sync()
        # 模拟 A 时钟偏快 500s：把 A 侧已知修订抬高（等价于
        # B 拉到一个"未来"的 rev_ts）
        uuid = db.list_memos()[0].uuid
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        with db.db() as conn:
            conn.execute(
                "UPDATE sync_entity_state SET rev_ts = rev_ts + 500"
                " WHERE entity_uuid = ?", (uuid,))
            bumped = conn.execute(
                "SELECT rev_ts FROM sync_entity_state"
                " WHERE entity_uuid = ?", (uuid,)).fetchone()["rev_ts"]
        bmid = next(m.id for m in b.memos() if m.uuid == uuid)
        db.update_content(bmid, "B的迟到编辑")
        # B 的新 rev_ts 必须 > 被抬高的已知修订（逻辑抬升生效）
        with db.db() as conn:
            row = conn.execute(
                "SELECT rev_ts FROM sync_outbox ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        self.assertGreater(row["rev_ts"], bumped)
        a.sync(); b.sync(); a.sync()
        ac = next(m.content for m in a.memos() if m.uuid == uuid)
        self.assertEqual(ac, "B的迟到编辑")

    def test_commit_conflict_detected_not_overwritten(self) -> None:
        """SYNC-005：同 seq 不同内容的远端 commit 绝不被覆盖。"""
        a, _b = self._dev("A"), self._dev("B")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.add_memo("冲突测试")
        # 预置同 writer seq=1 但内容不同的 commit
        with db.db() as conn:
            wid = store.profile_writer_id(conn)
        bad_path = f"/dav/memo-sync/{VID}/commits/{wid}/1.enc"
        from test_sync_transport import FakeWebDAV
        FakeWebDAV.store[bad_path] = b"different-content"
        from memo_app.sync.engine import SyncError
        with self.assertRaises(SyncError):
            a.sync()
        # 原对象未被破坏
        self.assertEqual(FakeWebDAV.store[bad_path], b"different-content")

    def test_keep_mode_preserves_conflict_copy(self) -> None:
        """keep 冲突模式：落败的远端修订存为【冲突副本】本地备忘。"""
        a = self._dev("A")
        b = _Device(self.port, "B", conflict_mode="keep")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("共享条目")
        a.sync(); b.sync()
        uuid = db.list_memos()[0].uuid
        # 双方离线各自修改
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        db.update_content(mid, "A的版本")
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        bmid = next(m.id for m in b.memos() if m.uuid == uuid)
        db.update_content(bmid, "B的版本")
        a.sync()
        b.sync()  # B 拉 A 的修改：落败版本应存为冲突副本
        b_memos = b.memos()
        copies = [m for m in b_memos if m.content.startswith("【冲突副本】")]
        canonical = [
            m for m in b_memos if not m.content.startswith("【冲突副本】")
        ]
        self.assertEqual(len(canonical), 1)   # 正主只有一条
        self.assertEqual(len(copies), 1)      # 落败版本恰好一个副本
        # 副本内容 = 落败版本正文 + 标记，且不带回收站/完成态
        self.assertIn("冲突副本", copies[0].content)
        self.assertFalse(copies[0].is_done)

    def test_keep_mode_mirror_updates_no_copies(self) -> None:
        """keep 模式：本地无编辑时远端顺序更新不产冲突副本——
        本地行只是镜像，不算冲突。"""
        a = self._dev("A")
        b = _Device(self.port, "B", conflict_mode="keep")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("共享条目")
        a.sync(); b.sync()
        for txt in ("v2", "v3"):
            os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
            db.update_content(mid, txt)
            a.sync()
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        b.sync()
        copies = [
            m for m in b.memos() if m.content.startswith("【冲突副本】")
        ]
        self.assertEqual(copies, [])
        self.assertEqual(len(b.memos()), 1)
        self.assertEqual(b.memos()[0].content, "v3")

    def test_keep_mode_remote_delete_preserves_local(self) -> None:
        """keep 模式：远端删除胜出时，本机撰写的版本存为副本。"""
        a = self._dev("A")
        b = _Device(self.port, "B", conflict_mode="keep")
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        mid = db.add_memo("将被删除")
        a.sync(); b.sync()
        uuid = db.list_memos()[0].uuid
        # B 先离线修改并发布
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        bmid = next(m.id for m in b.memos() if m.uuid == uuid)
        db.update_content(bmid, "B的未删版本")
        b.sync()
        # 把 B 的已知修订调旧，保证 A 的删除修订确定性胜出
        # （避免同秒 rev_ts 平局落到 writer_id 字典序）
        with db.db() as conn:
            conn.execute(
                "UPDATE sync_entity_state SET rev_ts = rev_ts - 100"
                " WHERE entity_uuid = ?", (uuid,))
        # A 拉取 B 的修改后硬删（delete 修订时间更晚，必胜）
        os.environ["MEMO_APP_DATA_DIR"] = a.dir.name
        amid = next(m.id for m in a.memos() if m.uuid == uuid)
        db.purge(amid)
        a.sync()
        # B 再同步：删除胜出，本地版本应留冲突副本
        os.environ["MEMO_APP_DATA_DIR"] = b.dir.name
        b.sync()
        b_memos = b.memos()
        copies = [m for m in b_memos if m.content.startswith("【冲突副本】")]
        canonical = [
            m for m in b_memos if not m.content.startswith("【冲突副本】")
        ]
        self.assertEqual(canonical, [])          # 正主被删除
        self.assertEqual(len(copies), 1)         # 本地版本留了副本
        self.assertIn("B的未删版本", copies[0].content)


if __name__ == "__main__":
    unittest.main()
