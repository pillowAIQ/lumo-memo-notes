"""SQLite 数据层。

memos 表字段说明：
- is_pinned / is_done / deleted_at 为后续功能（置顶、待办、回收站）预留，
  MVP 阶段已启用置顶、完成、软删除。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

DEFAULT_DATA_DIR = Path.home() / ".memo_app"


def data_dir() -> Path:
    return Path(os.environ.get("MEMO_APP_DATA_DIR", DEFAULT_DATA_DIR))


def db_path() -> Path:
    return data_dir() / "memo.db"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Memo:
    id: int
    content: str
    is_pinned: bool
    is_done: bool
    created_at: str
    updated_at: str
    deleted_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Memo":
        return cls(
            id=row["id"],
            content=row["content"],
            is_pinned=bool(row["is_pinned"]),
            is_done=bool(row["is_done"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        )


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                is_done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_memos_list
                ON memos(deleted_at, is_pinned DESC, is_done, created_at DESC);
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                collapsed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memo_categories (
                memo_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                PRIMARY KEY (memo_id, category_id)
            );
            """
        )


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """事务 + 关闭 的连接上下文（with connect() 只 commit 不 close）。"""
    with closing(connect()) as conn, conn:
        yield conn


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def add_memo(content: str) -> int:
    content = content.strip()
    if not content:
        raise ValueError("memo content is empty")
    ts = now_iso()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO memos(content, created_at, updated_at) VALUES (?, ?, ?)",
            (content, ts, ts),
        )
        return int(cur.lastrowid)


def update_content(memo_id: int, content: str) -> None:
    content = content.strip()
    if not content:
        raise ValueError("memo content is empty")
    with db() as conn:
        conn.execute(
            "UPDATE memos SET content = ?, updated_at = ? WHERE id = ?",
            (content, now_iso(), memo_id),
        )


def list_memos(*, trash: bool = False, query: str = "") -> list[Memo]:
    clauses = ["deleted_at IS NOT NULL" if trash else "deleted_at IS NULL"]
    params: list[object] = []
    if query.strip():
        clauses.append("content LIKE ? ESCAPE '\\'")
        params.append(f"%{_like_escape(query.strip())}%")
    sql = (
        "SELECT * FROM memos WHERE "
        + " AND ".join(clauses)
        + " ORDER BY is_pinned DESC, is_done ASC, created_at DESC, id DESC"
    )
    with db() as conn:
        return [Memo.from_row(r) for r in conn.execute(sql, params)]


def _touch(memo_id: int, **fields: object) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db() as conn:
        conn.execute(
            f"UPDATE memos SET {sets}, updated_at = ? WHERE id = ?",
            (*fields.values(), now_iso(), memo_id),
        )


def toggle_pin(memo_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE memos SET is_pinned = 1 - is_pinned, updated_at = ? WHERE id = ?",
            (now_iso(), memo_id),
        )


def toggle_done(memo_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE memos SET is_done = 1 - is_done, updated_at = ? WHERE id = ?",
            (now_iso(), memo_id),
        )


def soft_delete(memo_id: int) -> None:
    _touch(memo_id, deleted_at=now_iso())


def restore(memo_id: int) -> None:
    _touch(memo_id, deleted_at=None)


def purge(memo_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))


def purge_all_trash() -> int:
    with db() as conn:
        cur = conn.execute("DELETE FROM memos WHERE deleted_at IS NOT NULL")
        return int(cur.rowcount or 0)


# ---------- 分类（文件夹） ----------


@dataclass
class Category:
    id: int
    name: str
    collapsed: bool


def list_categories() -> list[Category]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, collapsed FROM categories ORDER BY id"
        ).fetchall()
    return [Category(r["id"], r["name"], bool(r["collapsed"])) for r in rows]


def create_category(name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("category name is empty")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO categories(name, created_at) VALUES (?, ?)",
            (name, now_iso()),
        )
        return int(cur.lastrowid)


def delete_category(cat_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM memo_categories WHERE category_id = ?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))


def set_category_collapsed(cat_id: int, collapsed: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE categories SET collapsed = ? WHERE id = ?",
            (1 if collapsed else 0, cat_id),
        )


def find_or_create_category(name: str) -> int:
    """按名字找分类，没有则创建，返回 id。"""
    name = name.strip()
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
    return create_category(name)


def set_memo_categories(memo_id: int, cat_ids: list[int]) -> None:
    with db() as conn:
        conn.execute("DELETE FROM memo_categories WHERE memo_id = ?", (memo_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO memo_categories(memo_id, category_id) VALUES (?, ?)",
            [(memo_id, c) for c in cat_ids],
        )


def memo_category_map() -> dict[int, set[int]]:
    """memo_id -> {category_id, ...}"""
    out: dict[int, set[int]] = {}
    with db() as conn:
        for mid, cid in conn.execute("SELECT memo_id, category_id FROM memo_categories"):
            out.setdefault(int(mid), set()).add(int(cid))
    return out
