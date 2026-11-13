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
    deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity, entity_uuid)
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
    rev_ts = time.time()
    wid = _writer_id(conn)
    env = {
        "v": 1,
        "entity": entity,
        "entity_uuid": entity_uuid,
        "op": op,
        "rev_ts": rev_ts,
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
    # 本地写入即最新修订；LWW 比较键 (rev_ts, writer_id)
    conn.execute(
        "INSERT INTO sync_entity_state"
        "(entity, entity_uuid, rev_ts, writer_id, deleted)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(entity, entity_uuid) DO UPDATE SET"
        " rev_ts = excluded.rev_ts,"
        " writer_id = excluded.writer_id,"
        " deleted = excluded.deleted",
        (entity, entity_uuid, rev_ts, wid, 1 if op == "delete" else 0),
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
