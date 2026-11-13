"""M1 同步数据层不变量测试。

运行：./.venv/Scripts/python.exe -m unittest discover -s tests -v
每个测试使用独立临时 MEMO_APP_DATA_DIR，不碰真实数据。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memo_app import db  # noqa: E402


class SyncStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MEMO_APP_DATA_DIR"] = self._tmp.name
        db.init_db()

    def tearDown(self) -> None:
        os.environ.pop("MEMO_APP_DATA_DIR", None)
        self._tmp.cleanup()

    def outbox(self) -> list:
        with db.db() as conn:
            return conn.execute(
                "SELECT entity, entity_uuid, op, payload"
                " FROM sync_outbox ORDER BY seq"
            ).fetchall()

    def test_add_memo_with_category_atomic(self) -> None:
        mid = db.add_memo("测试", cat_name="工作")
        rows = self.outbox()
        self.assertEqual(len(rows), 2)
        memo_env = json.loads(
            next(r for r in rows if r["entity"] == "memo")["payload"]
        )
        cat_env = json.loads(
            next(r for r in rows if r["entity"] == "category")["payload"]
        )
        self.assertEqual(memo_env["data"]["content"], "测试")
        self.assertEqual(
            memo_env["data"]["category_uuids"], [cat_env["entity_uuid"]]
        )
        self.assertNotIn("notified", memo_env["data"])
        self.assertTrue(memo_env["entity_uuid"])
        self.assertEqual(memo_env["op"], "upsert")
        self.assertIsNotNone(mid)

    def test_uuid_stable_and_unique_index(self) -> None:
        db.add_memo("a")
        db.add_memo("b")
        uuids = [m.uuid for m in db.list_memos()]
        self.assertEqual(len(set(uuids)), 2)
        self.assertTrue(all(len(u) == 36 for u in uuids))

    def test_mark_notified_no_sync_change(self) -> None:
        mid = db.add_memo("提醒测试")
        before = len(self.outbox())
        updated = db.list_memos()[0].updated_at
        db.mark_notified(mid)
        m = db.list_memos()[0]
        self.assertEqual(m.updated_at, updated)
        self.assertEqual(m.notified, 1)
        self.assertEqual(len(self.outbox()), before)

    def test_all_writes_enqueue(self) -> None:
        mid = db.add_memo("x")
        before = len(self.outbox())
        db.update_content(mid, "y")
        db.toggle_pin(mid)
        db.toggle_done(mid)          # 普通备忘录 -> 进回收站
        db.restore(mid)
        db.set_task_props(mid, priority=3, due_ts=1893456000)
        db.soft_delete(mid)
        # add(1) + 6 次写 = 7 行新 outbox
        self.assertEqual(len(self.outbox()), before + 6)

    def test_edit_memo_single_revision(self) -> None:
        mid = db.add_memo("旧内容")
        before = len(self.outbox())
        db.edit_memo(mid, "新内容", {"priority": 2, "due_ts": 1893456000})
        new = self.outbox()[before:]
        self.assertEqual(len(new), 1)  # 一次用户编辑 = 一条修订
        env = json.loads(new[0]["payload"])
        self.assertEqual(env["data"]["content"], "新内容")
        self.assertEqual(env["data"]["priority"], 2)

    def test_purge_delete_op_and_tombstone(self) -> None:
        mid = db.add_memo("待删")
        uuid = db.list_memos()[0].uuid
        db.purge(mid)
        last = self.outbox()[-1]
        self.assertEqual(last["op"], "delete")
        self.assertEqual(last["entity_uuid"], uuid)
        with db.db() as conn:
            st = conn.execute(
                "SELECT deleted FROM sync_entity_state"
                " WHERE entity='memo' AND entity_uuid=?",
                (uuid,),
            ).fetchone()
        self.assertIsNotNone(st)
        self.assertEqual(st["deleted"], 1)

    def test_purge_all_trash(self) -> None:
        m1 = db.add_memo("a")
        db.add_memo("b")
        db.soft_delete(m1)
        n = db.purge_all_trash()
        self.assertEqual(n, 1)
        self.assertEqual(self.outbox()[-1]["op"], "delete")

    def test_delete_category_cascades_sync(self) -> None:
        db.add_memo("在分类里", cat_name="生活")
        cid = next(c.id for c in db.list_categories() if c.name == "生活")
        before = len(self.outbox())
        db.delete_category(cid)
        new = self.outbox()[before:]
        ops = {(r["entity"], r["op"]) for r in new}
        self.assertIn(("memo", "upsert"), ops)
        self.assertIn(("category", "delete"), ops)
        memo_env = json.loads(
            next(r for r in new if r["entity"] == "memo")["payload"]
        )
        self.assertIsNotNone(memo_env["data"]["deleted_at"])
        self.assertEqual(memo_env["data"]["category_uuids"], [])

    def test_init_db_idempotent_uuid_stable(self) -> None:
        db.add_memo("稳定")
        u1 = db.list_memos()[0].uuid
        db.init_db()
        db.init_db()
        self.assertEqual(db.list_memos()[0].uuid, u1)

    def test_baseline_entity_state_for_existing_rows(self) -> None:
        # 模拟迁移前数据：清掉 entity_state 后重跑 init_db，
        # 基线循环必须重建（add_memo 的 enqueue 不参与此路径）
        db.add_memo("已有数据")
        uuid = db.list_memos()[0].uuid
        with db.db() as conn:
            conn.execute("DELETE FROM sync_entity_state")
        db.init_db()
        with db.db() as conn:
            st = conn.execute(
                "SELECT rev_ts, writer_id, deleted FROM sync_entity_state"
                " WHERE entity='memo' AND entity_uuid=?",
                (uuid,),
            ).fetchone()
        self.assertIsNotNone(st)
        self.assertGreater(st["rev_ts"], 0)
        self.assertTrue(st["writer_id"])
        self.assertEqual(st["deleted"], 0)

    def test_toggle_done_recur_advances_and_syncs(self) -> None:
        import time
        mid = db.add_memo(
            "重复任务", due_ts=int(time.time()) + 3600, recur="daily"
        )
        before = len(self.outbox())
        db.toggle_done(mid)
        m = db.list_memos()[0]
        self.assertIsNotNone(m.due_ts)
        self.assertGreater(m.due_ts, int(time.time()))
        self.assertEqual(len(self.outbox()), before + 1)


if __name__ == "__main__":
    unittest.main()
