"""同步本地存储层（M1）：稳定 UUID、outbox、LWW 修订状态。

对应 docs/jianguoyun-sync-plan.md §4.4：
- memos/categories 增加 uuid 列，跨设备身份不依赖整数主键；
- 业务写入与 outbox 入队在同一事务提交（db.py 各写函数内调用 enqueue_*）；
- sync_entity_state 记录每实体最近修订 (rev_ts, writer_id)，
  供后续接收端做后写覆盖（LWW）判定；
- purge 写 op='delete' 并在 entity_state 保留 tombstone，防止旧修订复活；
- 本机专属字段（notified、分类折叠、窗口/挂件状态）不进 payload。

注意：writer_id 目前与 device_id 相同、持久化在数据库内。
文档 §4.3 要求克隆数据目录后换新 writer 世代——该检测属后续里程碑。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid as uuidlib
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    device_id TEXT NOT NULL,
    writer_id TEXT NOT NULL,
    vault_id TEXT,
    protocol_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    entity_uuid TEXT NOT NULL,
    op TEXT NOT NULL,
    rev_ts REAL NOT NULL,
    writer_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_entity
    ON sync_outbox(entity, entity_uuid);
CREATE TABLE IF NOT EXISTS sync_entity_state (
    entity TEXT NOT NULL,
    entity_uuid TEXT NOT NULL,
    rev_ts REAL NOT NULL,
    writer_id TEXT NOT NULL,
    rev_seq INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity, entity_uuid)
);
CREATE TABLE IF NOT EXISTS sync_pending_links (
    memo_uuid TEXT NOT NULL,
    category_uuid TEXT NOT NULL,
    PRIMARY KEY (memo_uuid, category_uuid)
);
CREATE INDEX IF NOT EXISTS idx_pending_links_cat
    ON sync_pending_links(category_uuid);
CREATE TABLE IF NOT EXISTS sync_peer_state (
    writer_id TEXT PRIMARY KEY,
    head_seq INTEGER NOT NULL DEFAULT 0,
    applied_seq INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sync_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    writer_id TEXT NOT NULL,
    batch_seq INTEGER NOT NULL,
    blob BLOB NOT NULL,
    digest TEXT NOT NULL,
    outbox_seqs TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(writer_id, batch_seq)
);
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_uuid() -> str:
    return str(uuidlib.uuid4())


def ensure_schema(conn: sqlite3.Connection) -> None:
    """创建同步表、补 uuid 列并回填、确保 profile 行。幂等，可重复执行。"""
    conn.executescript(SCHEMA)
    for table in ("memos", "categories"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "uuid" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN uuid TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_memos_uuid "
        "ON memos(uuid) WHERE uuid IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_uuid "
        "ON categories(uuid) WHERE uuid IS NOT NULL"
    )
    # 回填存量行；重跑时 uuid 已非空，不会重复生成
    for table in ("memos", "categories"):
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE uuid IS NULL OR uuid = ''"
        ).fetchall()
        for r in rows:
            conn.execute(
                f"UPDATE {table} SET uuid = ? WHERE id = ?",
                (new_uuid(), r["id"]),
            )
    # outbox.sent 列（0=待发 1=已打包 2=已发布）：旧库补列
    ob_cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(sync_outbox)")
    }
    if "sent" not in ob_cols:
        conn.execute(
            "ALTER TABLE sync_outbox ADD COLUMN sent INTEGER NOT NULL DEFAULT 0"
        )
    # entity_state.rev_seq：同 writer 同 rev_ts 的因果序（outbox seq）
    es_cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(sync_entity_state)")
    }
    if "rev_seq" not in es_cols:
        conn.execute(
            "ALTER TABLE sync_entity_state"
            " ADD COLUMN rev_seq INTEGER NOT NULL DEFAULT 0"
        )
    # profile.bootstrapped：存量数据是否已入 outbox（首次同步全量回填）
    pf_cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(sync_profile)")
    }
    if "bootstrapped" not in pf_cols:
        conn.execute(
            "ALTER TABLE sync_profile"
            " ADD COLUMN bootstrapped INTEGER NOT NULL DEFAULT 0"
        )
    if conn.execute("SELECT id FROM sync_profile WHERE id = 1").fetchone() is None:
        dev = new_uuid()
        conn.execute(
            "INSERT INTO sync_profile"
            "(id, device_id, writer_id, vault_id, protocol_version, created_at)"
            " VALUES (1, ?, ?, NULL, 1, ?)",
            (dev, dev, _now_iso()),
        )
    # 存量实体建立 LWW 基线（文档 §8.3）：迁移前已存在的行补一条
    # entity_state，rev_ts 取迁移时刻；已有 state 的行不覆盖，幂等。
    wid = _writer_id(conn)
    now_ts = time.time()
    for table, entity in (("memos", "memo"), ("categories", "category")):
        conn.execute(
            "INSERT INTO sync_entity_state"
            "(entity, entity_uuid, rev_ts, writer_id, deleted)"
            f" SELECT ?, uuid, ?, ?, 0 FROM {table}"
            " WHERE uuid IS NOT NULL AND NOT EXISTS"
            " (SELECT 1 FROM sync_entity_state s"
            f"  WHERE s.entity = ? AND s.entity_uuid = {table}.uuid)",
            (entity, now_ts, wid, entity),
        )


def _writer_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT writer_id FROM sync_profile WHERE id = 1"
    ).fetchone()
    if row is None:
        # profile 缺失（手工删行/部分迁移）：现场重建，保证业务写不被拖垮
        dev = new_uuid()
        conn.execute(
            "INSERT INTO sync_profile"
            "(id, device_id, writer_id, vault_id, protocol_version, created_at)"
            " VALUES (1, ?, ?, NULL, 1, ?)",
            (dev, dev, _now_iso()),
        )
        return dev
    return str(row["writer_id"])


def enqueue(
    conn: sqlite3.Connection,
    *,
    entity: str,
    entity_uuid: str,
    op: str,
    payload: dict,
) -> None:
    """写入 outbox + 更新本地修订状态。必须在业务写入的同一事务内调用。"""
    # rev_ts = max(墙钟, 已知修订+ε)：保证本机新修订在逻辑上
    # 严格晚于已应用的远端修订——否则时钟偏慢的设备会产生
    # "因果更新但版本更小"的修订被对端永久拒收（SYNC-001）
    rev_ts = time.time()
    cur = conn.execute(
        "SELECT rev_ts FROM sync_entity_state"
        " WHERE entity = ? AND entity_uuid = ?",
        (entity, entity_uuid),
    ).fetchone()
    if cur is not None and cur["rev_ts"] >= rev_ts:
        rev_ts = float(cur["rev_ts"]) + 0.001
    wid = _writer_id(conn)
    # rev_seq：同 writer 同 rev_ts（时钟粒度/回拨）时的因果序。
    # 用 sqlite_sequence（AUTOINCREMENT 保证单调）而非 MAX(seq)+1，
    # 防止 outbox 被清空后序号回退。
    seq_row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'sync_outbox'"
    ).fetchone()
    rev_seq = int(seq_row["seq"]) + 1 if seq_row else 1
    env = {
        "v": 1,
        "entity": entity,
        "entity_uuid": entity_uuid,
        "op": op,
        "rev_ts": rev_ts,
        "rev_seq": rev_seq,
        "writer_id": wid,
        "data": payload,
    }
    conn.execute(
        "INSERT INTO sync_outbox"
        "(entity, entity_uuid, op, rev_ts, writer_id, payload, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            entity,
            entity_uuid,
            op,
            rev_ts,
            wid,
            json.dumps(env, ensure_ascii=False),
            _now_iso(),
        ),
    )
    # 本地写入即最新修订；LWW 比较键 (rev_ts, writer_id, rev_seq)
    conn.execute(
        "INSERT INTO sync_entity_state"
        "(entity, entity_uuid, rev_ts, writer_id, rev_seq, deleted)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(entity, entity_uuid) DO UPDATE SET"
        " rev_ts = excluded.rev_ts,"
        " writer_id = excluded.writer_id,"
        " rev_seq = excluded.rev_seq,"
        " deleted = excluded.deleted",
        (
            entity, entity_uuid, rev_ts, wid, rev_seq,
            1 if op == "delete" else 0,
        ),
    )


def enqueue_memo(conn: sqlite3.Connection, memo_id: int) -> None:
    """以 memo 当前行状态生成 upsert payload 入队。行不存在则跳过。"""
    row = conn.execute("SELECT * FROM memos WHERE id = ?", (memo_id,)).fetchone()
    if row is None:
        return
    cats = [
        r["uuid"]
        for r in conn.execute(
            "SELECT c.uuid AS uuid FROM memo_categories mc"
            " JOIN categories c ON c.id = mc.category_id"
            " WHERE mc.memo_id = ? ORDER BY c.uuid",
            (memo_id,),
        )
    ]
    payload = {
        "content": row["content"],
        "is_pinned": bool(row["is_pinned"]),
        "is_done": bool(row["is_done"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
        "due_ts": row["due_ts"],
        "priority": row["priority"],
        "recur": row["recur"],
        "remind_advance": row["remind_advance"],
        "category_uuids": cats,
    }
    # 本地写入后旧 pending 失效：最新 category_uuids 以本 op 为准，
    # 避免远端分类晚到时按过期引用回填
    drop_pending_links_for_memo(conn, row["uuid"])
    enqueue(
        conn,
        entity="memo",
        entity_uuid=row["uuid"],
        op="upsert",
        payload=payload,
    )


def enqueue_memo_delete(conn: sqlite3.Connection, memo_uuid: str) -> None:
    enqueue(conn, entity="memo", entity_uuid=memo_uuid, op="delete", payload={})


def enqueue_category(conn: sqlite3.Connection, cat_id: int) -> None:
    row = conn.execute(
        "SELECT * FROM categories WHERE id = ?", (cat_id,)
    ).fetchone()
    if row is None:
        return
    enqueue(
        conn,
        entity="category",
        entity_uuid=row["uuid"],
        op="upsert",
        payload={"name": row["name"], "created_at": row["created_at"]},
    )


def enqueue_category_delete(
    conn: sqlite3.Connection, cat_uuid: str
) -> None:
    enqueue(conn, entity="category", entity_uuid=cat_uuid, op="delete", payload={})


_ENTITY_TABLES = {"memo": "memos", "category": "categories"}


# ---------- outbox / 批次 / 对端状态（engine 用） ----------


def pending_outbox(conn: sqlite3.Connection, limit: int) -> list:
    return conn.execute(
        "SELECT seq, payload FROM sync_outbox WHERE sent = 0"
        " ORDER BY seq LIMIT ?",
        (limit,),
    ).fetchall()


def mark_outbox_sent(conn: sqlite3.Connection, seqs: list[int], sent: int) -> None:
    conn.executemany(
        "UPDATE sync_outbox SET sent = ? WHERE seq = ?", [(sent, s) for s in seqs]
    )


def next_batch_seq(conn: sqlite3.Connection, writer_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(batch_seq), 0) + 1 AS n FROM sync_batches"
        " WHERE writer_id = ?",
        (writer_id,),
    ).fetchone()
    return int(row["n"])


def save_batch(
    conn: sqlite3.Connection,
    *,
    writer_id: str,
    batch_seq: int,
    blob: bytes,
    digest: str,
    outbox_seqs: list[int],
) -> int:
    """持久化待发批次（重试复用同一 blob）。返回批次行 id。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO sync_batches"
        "(writer_id, batch_seq, blob, digest, outbox_seqs, published, created_at)"
        " VALUES (?, ?, ?, ?, ?, 0, ?)",
        (
            writer_id,
            batch_seq,
            blob,
            digest,
            json.dumps(outbox_seqs),
            _now_iso(),
        ),
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    # (writer_id, batch_seq) 冲突：必须是同一内容的重试，
    # 否则说明 seq 分配出现并发/恢复异常——响亮失败而非静默复用
    row = conn.execute(
        "SELECT id, digest FROM sync_batches"
        " WHERE writer_id = ? AND batch_seq = ?",
        (writer_id, batch_seq),
    ).fetchone()
    if row is None or row["digest"] != digest:
        raise ValueError(
            f"batch seq conflict: writer={writer_id} seq={batch_seq}"
        )
    return int(row["id"])


def has_unpublished(conn: sqlite3.Connection, writer_id: str) -> bool:
    """是否存在已打包未发布的批次（轻量 EXISTS，不取 blob）。"""
    return conn.execute(
        "SELECT 1 FROM sync_batches WHERE writer_id = ?"
        " AND published = 0 LIMIT 1",
        (writer_id,),
    ).fetchone() is not None


def unpublished_batches(conn: sqlite3.Connection, writer_id: str) -> list:
    return conn.execute(
        "SELECT id, batch_seq, blob, outbox_seqs FROM sync_batches"
        " WHERE writer_id = ? AND published = 0 ORDER BY batch_seq",
        (writer_id,),
    ).fetchall()


def mark_batch_published(conn: sqlite3.Connection, batch_row_id: int) -> None:
    conn.execute(
        "UPDATE sync_batches SET published = 1 WHERE id = ?", (batch_row_id,)
    )


def peer_state(conn: sqlite3.Connection, writer_id: str) -> tuple[int, int]:
    """返回 (head_seq, applied_seq)；无记录视为 (0,0)。"""
    row = conn.execute(
        "SELECT head_seq, applied_seq FROM sync_peer_state WHERE writer_id = ?",
        (writer_id,),
    ).fetchone()
    if row is None:
        return 0, 0
    return int(row["head_seq"]), int(row["applied_seq"])


def set_peer_state(
    conn: sqlite3.Connection,
    writer_id: str,
    *,
    head_seq: int | None = None,
    applied_seq: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO sync_peer_state(writer_id, head_seq, applied_seq)"
        " VALUES (?, ?, ?)"
        " ON CONFLICT(writer_id) DO UPDATE SET"
        # head_seq 只增不减：记录"观察到的最大 head"，
        # 云端 head 回退由 engine 在拉取前另行检测
        " head_seq = MAX(COALESCE(?, 0), sync_peer_state.head_seq),"
        " applied_seq = COALESCE(?, sync_peer_state.applied_seq)",
        (
            writer_id,
            head_seq or 0,
            applied_seq or 0,
            head_seq,
            applied_seq,
        ),
    )


def remote_state(
    conn: sqlite3.Connection, entity: str, entity_uuid: str
) -> tuple[float, str, int] | None:
    """已知修订的 (rev_ts, writer_id, rev_seq)；无记录返回 None。"""
    row = conn.execute(
        "SELECT rev_ts, writer_id, rev_seq FROM sync_entity_state"
        " WHERE entity = ? AND entity_uuid = ?",
        (entity, entity_uuid),
    ).fetchone()
    if row is None:
        return None
    return float(row["rev_ts"]), str(row["writer_id"]), int(row["rev_seq"])


def record_remote_state(
    conn: sqlite3.Connection,
    *,
    entity: str,
    entity_uuid: str,
    rev_ts: float,
    rev_seq: int,
    writer_id: str,
    deleted: bool,
) -> None:
    """接收端记录远端修订为当前状态（仅在判定应用后调用）。"""
    conn.execute(
        "INSERT INTO sync_entity_state"
        "(entity, entity_uuid, rev_ts, writer_id, rev_seq, deleted)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(entity, entity_uuid) DO UPDATE SET"
        " rev_ts = excluded.rev_ts,"
        " writer_id = excluded.writer_id,"
        " rev_seq = excluded.rev_seq,"
        " deleted = excluded.deleted",
        (
            entity, entity_uuid, rev_ts, writer_id, rev_seq,
            1 if deleted else 0,
        ),
    )


def lww_newer(
    inc: tuple[float, str, int], cur: tuple[float, str, int] | None
) -> bool:
    """后写覆盖判定：仅当新修订严格大于当前已知修订才应用。

    比较键 (rev_ts, writer_id, rev_seq) 字典序；
    rev_seq 解决同 writer 同 rev_ts（时钟粒度/回拨）的因果平局。
    cur=None 表示未见过该实体。
    """
    if cur is None:
        return True
    return inc > cur


def profile_writer_id(conn: sqlite3.Connection) -> str:
    return _writer_id(conn)


def profile_vault_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT vault_id FROM sync_profile WHERE id = 1"
    ).fetchone()
    return row["vault_id"] if row and row["vault_id"] else None


def set_profile_vault_id(conn: sqlite3.Connection, vault_id: str) -> None:
    """绑定本机数据库到 vault（创建/加入成功后调用）。"""
    conn.execute(
        "UPDATE sync_profile SET vault_id = ? WHERE id = 1",
        (vault_id.lower(),),
    )


# ---------- 首次同步回填 / 跨 writer 分类依赖 ----------


def needs_bootstrap(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT bootstrapped FROM sync_profile WHERE id = 1"
    ).fetchone()
    return row is not None and not row["bootstrapped"]


def mark_bootstrapped(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE sync_profile SET bootstrapped = 1 WHERE id = 1"
    )


def bootstrap_outbox(conn: sqlite3.Connection) -> int:
    """把库中全部现存实体入队为 upsert（升级前数据首次同步）。

    分类先于备忘录入队，保证接收端解析 category_uuids 时分类已就位。
    返回入队条数。调用方负责随后 mark_bootstrapped。

    只回填"本机来源且从未入过 outbox"的实体：
    - state.writer_id != 本机 writer => 远端应用来的，不得回声发布
      （否则会以新 rev_ts 覆盖其他设备的更新）；
    - 已有任意 sent 状态的 outbox => 已随正常队列处理过。
    """
    wid = _writer_id(conn)
    n = 0
    for table, entity in (("categories", "category"), ("memos", "memo")):
        rows = conn.execute(
            f"SELECT t.id, t.uuid FROM {table} t"
            " JOIN sync_entity_state s"
            "   ON s.entity = ? AND s.entity_uuid = t.uuid"
            " WHERE s.writer_id = ?"
            " AND NOT EXISTS (SELECT 1 FROM sync_outbox o"
            "   WHERE o.entity = ? AND o.entity_uuid = t.uuid)",
            (entity, wid, entity),
        ).fetchall()
        for r in rows:
            if entity == "category":
                enqueue_category(conn, r["id"])
            else:
                enqueue_memo(conn, r["id"])
            n += 1
    return n


def add_pending_link(
    conn: sqlite3.Connection, memo_uuid: str, category_uuid: str
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sync_pending_links"
        "(memo_uuid, category_uuid) VALUES (?, ?)",
        (memo_uuid, category_uuid),
    )


def pending_link_rows(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT memo_uuid, category_uuid FROM sync_pending_links"
    ).fetchall()


def resolve_pending_links(conn: sqlite3.Connection, category_uuid: str) -> None:
    """某分类到达后，回填所有等待它的 memo 归属。"""
    cat = conn.execute(
        "SELECT id FROM categories WHERE uuid = ?", (category_uuid,)
    ).fetchone()
    if cat is None:
        return
    for r in conn.execute(
        "SELECT memo_uuid FROM sync_pending_links WHERE category_uuid = ?",
        (category_uuid,),
    ).fetchall():
        m = conn.execute(
            "SELECT id FROM memos WHERE uuid = ?", (r["memo_uuid"],)
        ).fetchone()
        if m is not None:
            conn.execute(
                "INSERT OR IGNORE INTO memo_categories"
                "(memo_id, category_id) VALUES (?, ?)",
                (m["id"], cat["id"]),
            )
    conn.execute(
        "DELETE FROM sync_pending_links WHERE category_uuid = ?",
        (category_uuid,),
    )


def drop_pending_links_for_category(
    conn: sqlite3.Connection, category_uuid: str
) -> None:
    conn.execute(
        "DELETE FROM sync_pending_links WHERE category_uuid = ?",
        (category_uuid,),
    )


def drop_pending_links_for_memo(
    conn: sqlite3.Connection, memo_uuid: str
) -> None:
    conn.execute(
        "DELETE FROM sync_pending_links WHERE memo_uuid = ?", (memo_uuid,)
    )


def entity_uuids(conn: sqlite3.Connection, entity: str) -> list[str]:
    """该实体下所有已知 uuid（业务表 + tombstone）。测试与诊断用。"""
    table = _ENTITY_TABLES[entity]
    out = {
        r["uuid"]
        for r in conn.execute(
            f"SELECT uuid FROM {table} WHERE uuid IS NOT NULL"
        )
    }
    out |= {
        r["entity_uuid"]
        for r in conn.execute(
            "SELECT entity_uuid FROM sync_entity_state WHERE entity = ?",
            (entity,),
        )
    }
    return sorted(out)
