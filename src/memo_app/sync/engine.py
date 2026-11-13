"""同步引擎（计划 §4.5）：发现 heads -> 下载批次 -> LWW 应用 ->
封装 outbox -> 顺序上传 -> 发布 head。

不依赖 GUI/网络实现细节：client 为 transport.WebDAVClient，
master_key 由调用方从 vault.json 解开后传入。

崩溃恢复约定：
- 批次 blob 在上传前已持久化到 sync_batches，重试复用同一字节；
- commit 全部上传成功后才发布 head（取最大 seq），中途崩溃由
  unpublished_batches 在下轮补齐；
- 应用以"每批次一个 BEGIN IMMEDIATE 事务"推进，applied_seq 与
  业务写入同生共死，中途崩溃从断点继续；
- 单条畸形/失败 op 记录修订状态后跳过（bad_ops 计数），不让
  毒化日志永久阻塞同步——所有端确定性地忽略同一 op。
"""
from __future__ import annotations

import json
import sqlite3

from .. import db
from . import crypto, protocol, store
from .transport import (
    NotFoundError,
    PreconditionFailedError,
    WebDAVError,
)

MAX_OPS_PER_BATCH = protocol.MAX_OPS_PER_BATCH
MAX_BATCHES_PER_SYNC = 20          # 单轮发布上限，余量留给下轮
_PACK_SAFE_BYTES = 3 * 1024 * 1024  # 打包估算上限（协议硬上限 4MiB）


class SyncError(Exception):
    """同步失败：远端数据不可信、协议错误、状态不一致。"""


class Engine:
    def __init__(self, client, master_key: bytes, vault_id: str):
        self.client = client
        self.master_key = master_key
        self.vault_id = vault_id
        with db.db() as conn:
            self.writer_id = store.profile_writer_id(conn)

    # ---------- 对外 ----------

    def sync_once(self) -> dict:
        """执行一轮完整同步。

        返回 {"pulled","applied_ops","bad_ops","published","bootstrapped"}。
        失败抛 SyncError / WebDAVError / CryptoError / ProtocolError /
        sqlite3.Error，调用方统一捕获处理。
        """
        stats = {
            "pulled": 0, "applied_ops": 0, "bad_ops": 0,
            "published": 0, "bootstrapped": 0,
        }
        p, a, b = self._pull()
        stats["pulled"], stats["applied_ops"], stats["bad_ops"] = p, a, b
        pub, boot = self._publish()
        stats["published"], stats["bootstrapped"] = pub, boot
        return stats

    # ---------- 拉取 ----------

    def _pull(self) -> tuple[int, int, int]:
        try:
            entries = self.client.propfind(protocol.HEADS_DIR)
        except NotFoundError:
            return 0, 0, 0  # heads/ 不存在 = 还没有任何写入者
        batches = ops = bad = 0
        for e in entries:
            if not e.rel.startswith(protocol.HEADS_DIR + "/"):
                continue
            name = e.rel.rsplit("/", 1)[-1]
            if not name.endswith(".enc"):
                continue
            wid = name[:-4]
            if not protocol.is_uuid(wid):
                raise SyncError(f"invalid writer head name: {name!r}")
            if wid == self.writer_id:
                continue  # 自己的日志不需要拉回来
            b, o, bd = self._pull_writer(wid)
            batches += b
            ops += o
            bad += bd
        # 跨 writer 依赖兜底：分类可能晚于引用它的 memo 到达，
        # pending_links 在此统一回填（按分类去重，幂等）
        with db.db() as conn:
            cats = {
                r["category_uuid"]
                for r in store.pending_link_rows(conn)
            }
            for cu in cats:
                store.resolve_pending_links(conn, cu)
        return batches, ops, bad

    def _pull_writer(self, wid: str) -> tuple[int, int, int]:
        head_blob = self.client.get(self._head_rel(wid))
        head = crypto.decrypt_json(
            self.master_key, head_blob, crypto.head_aad(self.vault_id, wid)
        )
        head = protocol.validate_head(head)
        if head["writer_id"] != wid:
            raise SyncError("head writer_id mismatch")

        with db.db() as conn:
            head_seen, applied = store.peer_state(conn, wid)
        head_seq = head["seq"]
        if head_seq < applied or head_seq < head_seen:
            # head 只应前进：回退意味着云端覆盖/篡改/本地状态损坏
            raise SyncError(
                f"remote head regressed for {wid}:"
                f" {head_seq} < seen={head_seen} applied={applied}"
            )
        batches = ops = bad = 0
        for seq in range(applied + 1, head_seq + 1):
            blob = self.client.get(self._commit_rel(wid, seq))
            batch = crypto.decrypt_json(
                self.master_key, blob,
                crypto.batch_aad(self.vault_id, wid, seq),
            )
            batch = protocol.validate_batch(batch)
            if batch["writer_id"] != wid or batch["seq"] != seq:
                raise SyncError("batch identity mismatch")
            a, bd = self._apply_batch(batch)
            ops += a
            bad += bd
            batches += 1
            with db.db() as conn:
                store.set_peer_state(
                    conn, wid, head_seq=head_seq, applied_seq=seq
                )
        return batches, ops, bad

    # ---------- 应用（LWW，不触发 outbox 回传） ----------

    def _apply_batch(self, batch: dict) -> tuple[int, int]:
        applied = bad = 0
        with db.db() as conn:
            # 立即取写锁：LWW 判定与落库在同一事务内，
            # 不会被并发 GUI 写插入造成状态回退
            conn.execute("BEGIN IMMEDIATE")
            for op in batch["ops"]:
                # savepoint 隔离：单 op 中途失败只回滚该 op，
                # 不会留下半截写入却被记为已应用
                conn.execute("SAVEPOINT sync_op")
                try:
                    if self._apply_op(conn, op):
                        applied += 1
                    conn.execute("RELEASE sync_op")
                except (KeyError, TypeError, ValueError,
                        OverflowError, sqlite3.Error):
                    conn.execute("ROLLBACK TO sync_op")
                    conn.execute("RELEASE sync_op")
                    # 毒化 op 不拖垮整条日志：记录为已见并跳过，
                    # 所有端确定性地忽略同一 op
                    bad += 1
                    try:
                        store.record_remote_state(
                            conn,
                            entity=op.get("entity", "memo"),
                            entity_uuid=op.get("entity_uuid", ""),
                            rev_ts=float(op.get("rev_ts", 0.0)),
                            rev_seq=int(op.get("rev_seq", 0)),
                            writer_id=op.get("writer_id", ""),
                            deleted=(op.get("op") == "delete"),
                        )
                    except (sqlite3.Error, ValueError, OverflowError):
                        pass
        return applied, bad

    def _apply_op(self, conn: sqlite3.Connection, op: dict) -> bool:
        entity, uuid = op["entity"], op["entity_uuid"]
        inc = (
            float(op["rev_ts"]),
            str(op["writer_id"]),
            int(op.get("rev_seq", 0)),
        )
        cur = store.remote_state(conn, entity, uuid)
        if not store.lww_newer(inc, cur):
            return False  # 已有相同或更新的修订：幂等丢弃
        if op["op"] == "delete":
            self._apply_delete(conn, entity, uuid)
            store.record_remote_state(
                conn, entity=entity, entity_uuid=uuid,
                rev_ts=inc[0], rev_seq=inc[2], writer_id=inc[1],
                deleted=True,
            )
            return True
        if entity == "memo":
            self._apply_memo(conn, uuid, op["data"])
        else:
            self._apply_category(conn, uuid, op["data"])
        store.record_remote_state(
            conn, entity=entity, entity_uuid=uuid,
            rev_ts=inc[0], rev_seq=inc[2], writer_id=inc[1],
            deleted=False,
        )
        return True

    def _apply_delete(self, conn, entity: str, uuid: str) -> None:
        if entity == "memo":
            store.drop_pending_links_for_memo(conn, uuid)
            row = conn.execute(
                "SELECT id FROM memos WHERE uuid = ?", (uuid,)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "DELETE FROM memo_categories WHERE memo_id = ?",
                    (row["id"],),
                )
                conn.execute("DELETE FROM memos WHERE id = ?", (row["id"],))
        else:
            store.drop_pending_links_for_category(conn, uuid)
            conn.execute(
                "DELETE FROM memo_categories WHERE category_id IN"
                " (SELECT id FROM categories WHERE uuid = ?)",
                (uuid,),
            )
            conn.execute("DELETE FROM categories WHERE uuid = ?", (uuid,))

    @staticmethod
    def _check_memo_data(d: dict) -> None:
        if not isinstance(d.get("content"), str):
            raise ValueError("memo content missing")
        for k in ("is_pinned", "is_done"):
            if not isinstance(d.get(k), bool):
                raise ValueError(f"memo {k} not bool")
        for k in ("created_at", "updated_at"):
            if not isinstance(d.get(k), str):
                raise ValueError(f"memo {k} not str")
        due = d.get("due_ts")
        if due is not None and (isinstance(due, bool)
                                or not isinstance(due, int)):
            raise ValueError("due_ts invalid")
        pr = d.get("priority", 0)
        if isinstance(pr, bool) or not isinstance(pr, int) or not 0 <= pr <= 3:
            raise ValueError("priority invalid")
        ra = d.get("remind_advance", 0)
        if isinstance(ra, bool) or not isinstance(ra, int) or ra < 0:
            raise ValueError("remind_advance invalid")
        rc = d.get("recur")
        if rc is not None and not isinstance(rc, str):
            raise ValueError("recur invalid")
        da = d.get("deleted_at")
        if da is not None and not isinstance(da, str):
            raise ValueError("deleted_at invalid")
        cus = d.get("category_uuids", [])
        if not isinstance(cus, list) or not all(
            protocol.is_uuid(c) for c in cus
        ):
            raise ValueError("category_uuids invalid")

    def _apply_memo(self, conn, uuid: str, d: dict) -> None:
        self._check_memo_data(d)
        # 新修订接管：清掉旧修订留下的 pending，按本 op 的
        # category_uuids 重建，避免过期引用在分类到达时错误回填
        store.drop_pending_links_for_memo(conn, uuid)
        row = conn.execute(
            "SELECT id, due_ts, remind_advance FROM memos WHERE uuid = ?",
            (uuid,),
        ).fetchone()
        due_ts = d.get("due_ts")
        adv = int(d.get("remind_advance", 0))
        fields = (
            d["content"], int(d["is_pinned"]), int(d["is_done"]),
            d["created_at"], d["updated_at"], d.get("deleted_at"),
            due_ts, int(d.get("priority", 0)),
            d.get("recur"), adv,
        )
        if row is not None:
            conn.execute(
                "UPDATE memos SET content=?, is_pinned=?, is_done=?,"
                " created_at=?, updated_at=?, deleted_at=?, due_ts=?,"
                " priority=?, recur=?, remind_advance=? WHERE id=?",
                (*fields, row["id"]),
            )
            mid = row["id"]
            # 远端改了截止/提前量：重置本机 notified，否则新截止
            # 永远不会再提醒（本地 set_task_props 同样语义）
            if (due_ts, adv) != (row["due_ts"], row["remind_advance"]):
                conn.execute(
                    "UPDATE memos SET notified = 0 WHERE id = ?", (mid,)
                )
        else:
            cur = conn.execute(
                "INSERT INTO memos(uuid, content, is_pinned, is_done,"
                " created_at, updated_at, deleted_at, due_ts, priority,"
                " recur, remind_advance) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid, *fields),
            )
            mid = int(cur.lastrowid)
        # 分类归属：按 uuid 解析本地 id；分类未到时登记 pending_links，
        # 由 _apply_category / _pull 末尾的 sweep 回填
        conn.execute("DELETE FROM memo_categories WHERE memo_id = ?", (mid,))
        for cu in d.get("category_uuids", []):
            cat = conn.execute(
                "SELECT id FROM categories WHERE uuid = ?", (cu,)
            ).fetchone()
            if cat is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO memo_categories"
                    "(memo_id, category_id) VALUES (?, ?)",
                    (mid, cat["id"]),
                )
            else:
                store.add_pending_link(conn, uuid, cu)

    def _apply_category(self, conn, uuid: str, d: dict) -> None:
        if not isinstance(d.get("name"), str) or not isinstance(
            d.get("created_at"), str
        ):
            raise ValueError("category data invalid")
        row = conn.execute(
            "SELECT id FROM categories WHERE uuid = ?", (uuid,)
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE categories SET name = ?, created_at = ? WHERE id = ?",
                (d["name"], d["created_at"], row["id"]),
            )
        else:
            # 同名分类（另一设备独立创建）：name UNIQUE 冲突时附加短码
            # 区分，保留身份不丢数据（计划 §5.1）
            name = d["name"]
            for suffix in ("", f"·{uuid[:4]}", f"·{uuid[:8]}"):
                try:
                    conn.execute(
                        "INSERT INTO categories(uuid, name, created_at)"
                        " VALUES (?, ?, ?)",
                        (uuid, name + suffix, d["created_at"]),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise ValueError("category name conflict unresolvable")
        # 分类到达：回填等待它的 memo 归属
        store.resolve_pending_links(conn, uuid)

    # ---------- 发布 ----------

    def _head_rel(self, wid: str) -> str:
        return f"{protocol.HEADS_DIR}/{wid}.enc"

    def _commit_rel(self, wid: str, seq: int) -> str:
        return f"{protocol.COMMITS_DIR}/{wid}/{seq}.enc"

    def _ensure_layout(self) -> None:
        """幂等创建 memo-sync/<vid>/{heads,commits,commits/<wid>}。"""
        self.client.mkcol_root()
        self.client.mkcol("")          # vault 根集合
        self.client.mkcol(protocol.HEADS_DIR)
        self.client.mkcol(protocol.COMMITS_DIR)
        self.client.mkcol(f"{protocol.COMMITS_DIR}/{self.writer_id}")

    def _publish(self) -> tuple[int, int]:
        published = bootstrapped = 0
        # 无活不发请求：空闲轮询不消耗 MKCOL/PUT 配额
        with db.db() as conn:
            has_work = (
                store.unpublished_batches(conn, self.writer_id)
                or store.pending_outbox(conn, 1)
                or store.needs_bootstrap(conn)
            )
        if not has_work:
            return 0, 0
        self._ensure_layout()

        # A. 崩溃恢复：先补齐所有已持久化未发布的批次。
        #    先全部 PUT commit，再只发布最大 seq 的 head——
        #    避免逐批 PUT head 把远端 head 打回低 seq。
        with db.db() as conn:
            pending_batches = store.unpublished_batches(conn, self.writer_id)
        if pending_batches:
            for b in pending_batches:
                self._upload_batch(b["batch_seq"], b["blob"])
            self._upload_head(pending_batches[-1]["batch_seq"])
            with db.db() as conn:
                for b in pending_batches:
                    store.mark_batch_published(conn, b["id"])
                    store.mark_outbox_sent(
                        conn, json.loads(b["outbox_seqs"]), sent=2
                    )
            published += len(pending_batches)

        # B. 首次同步：存量数据入队（分类先于备忘录）
        with db.db() as conn:
            if store.needs_bootstrap(conn):
                store.bootstrap_outbox(conn)
                store.mark_bootstrapped(conn)
                bootstrapped = 1

        # C. 打包 pending outbox -> 上传 -> 发布，直到发完或达上限
        for _ in range(MAX_BATCHES_PER_SYNC):
            with db.db() as conn:
                rows = store.pending_outbox(conn, MAX_OPS_PER_BATCH)
                if not rows:
                    break
                batch_row_id, batch_seq, blob, outbox_seqs = (
                    self._pack_batch(conn, rows)
                )
            # 网络请求在事务外
            self._upload_batch(batch_seq, blob)
            self._upload_head(batch_seq)
            with db.db() as conn:
                store.mark_batch_published(conn, batch_row_id)
                store.mark_outbox_sent(conn, outbox_seqs, sent=2)
            published += 1
        return published, bootstrapped

    def _pack_batch(self, conn, rows: list) -> tuple[int, int, bytes, list[int]]:
        """把 outbox 行打包成一个批次并持久化（sent=1）。

        按协议 4MiB 上限裁剪：装不下的尾部行留给下一批。
        单条 op 超限 -> SyncError（响亮失败，不静默卡死队列）。
        """
        ops_rows = list(rows)
        while True:
            ops = [json.loads(r["payload"]) for r in ops_rows]
            seq = store.next_batch_seq(conn, self.writer_id)
            try:
                batch = protocol.build_batch(self.writer_id, seq, ops)
                break
            except protocol.ProtocolError as e:
                if "too large" not in str(e) or len(ops_rows) <= 1:
                    raise SyncError(f"cannot pack batch: {e}") from e
                ops_rows = ops_rows[:-1]
        if not ops_rows:
            raise SyncError("cannot pack batch: empty after trim")
        blob = crypto.encrypt_json(
            self.master_key, batch,
            crypto.batch_aad(self.vault_id, self.writer_id, seq),
        )
        digest = protocol.batch_digest(batch)
        outbox_seqs = [r["seq"] for r in ops_rows]
        row_id = store.save_batch(
            conn, writer_id=self.writer_id, batch_seq=seq,
            blob=blob, digest=digest, outbox_seqs=outbox_seqs,
        )
        store.mark_outbox_sent(conn, outbox_seqs, sent=1)
        return row_id, seq, blob, outbox_seqs

    def _upload_batch(self, seq: int, blob: bytes) -> None:
        """上传 commit；同路径已存在时校验字节一致（重试幂等）。"""
        path = self._commit_rel(self.writer_id, seq)
        try:
            self.client.put(path, blob, create_only=True)
            return
        except PreconditionFailedError:
            pass
        # 已存在：必须与持久化 blob 逐字节一致，否则是序号冲突
        existing = self.client.get(path)
        if existing != blob:
            raise SyncError(
                f"commit conflict at seq {seq}: remote content differs"
            )

    def _upload_head(self, seq: int) -> None:
        head = protocol.build_head(self.writer_id, seq)
        blob = crypto.encrypt_json(
            self.master_key, head,
            crypto.head_aad(self.vault_id, self.writer_id),
        )
        self.client.put(self._head_rel(self.writer_id), blob)
