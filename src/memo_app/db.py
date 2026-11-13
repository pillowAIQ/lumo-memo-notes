"""SQLite 数据层。

memos 表字段说明：
- is_pinned / is_done / deleted_at 为后续功能（置顶、待办、回收站）预留，
  MVP 阶段已启用置顶、完成、软删除。
"""
from __future__ import annotations

import os
import sqlite3
import time
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
    due_ts: int | None = None      # 截止时间（Unix 秒，本地时区）
    priority: int = 0              # 0=无 1=低 2=中 3=高
    recur: str | None = None       # daily/weekly/monthly/yearly
    notified: int = 0              # 本次截止是否已提醒
    remind_advance: int = 0        # 提前提醒分钟数（0=到期时提醒）

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Memo":
        keys = set(row.keys())
        return cls(
            id=row["id"],
            content=row["content"],
            is_pinned=bool(row["is_pinned"]),
            is_done=bool(row["is_done"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
            due_ts=row["due_ts"] if "due_ts" in keys else None,
            priority=row["priority"] if "priority" in keys else 0,
            recur=row["recur"] if "recur" in keys else None,
            notified=row["notified"] if "notified" in keys else 0,
            remind_advance=row["remind_advance"] if "remind_advance" in keys else 0,
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
                deleted_at TEXT,
                due_ts INTEGER,
                priority INTEGER NOT NULL DEFAULT 0,
                recur TEXT,
                notified INTEGER NOT NULL DEFAULT 0,
                remind_advance INTEGER NOT NULL DEFAULT 0
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
        _migrate_memos(conn)


def _migrate_memos(conn: sqlite3.Connection) -> None:
    """旧库补列：due_ts / priority / recur / notified / remind_advance。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(memos)")}
    for col, ddl in (
        ("due_ts", "ALTER TABLE memos ADD COLUMN due_ts INTEGER"),
        ("priority", "ALTER TABLE memos ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"),
        ("recur", "ALTER TABLE memos ADD COLUMN recur TEXT"),
        ("notified", "ALTER TABLE memos ADD COLUMN notified INTEGER NOT NULL DEFAULT 0"),
        ("remind_advance", "ALTER TABLE memos ADD COLUMN remind_advance INTEGER NOT NULL DEFAULT 0"),
    ):
        if col not in cols:
            conn.execute(ddl)
    # 不变量：recur/remind_advance 依赖 due_ts，无截止的一律清掉
    conn.execute(
        "UPDATE memos SET recur = NULL, remind_advance = 0 WHERE due_ts IS NULL"
    )


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """事务 + 关闭 的连接上下文（with connect() 只 commit 不 close）。"""
    with closing(connect()) as conn, conn:
        yield conn


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def add_memo(
    content: str,
    *,
    due_ts: int | None = None,
    priority: int = 0,
    recur: str | None = None,
    remind_advance: int = 0,
) -> int:
    content = content.strip()
    if not content:
        raise ValueError("memo content is empty")
    priority = max(0, min(3, int(priority)))
    # recur/advance 依赖 due_ts：无截止时间时不存（避免后补截止静默生效）
    if due_ts is None:
        recur = None
        remind_advance = 0
    elif recur not in RECUR_LABELS:
        recur = None
    remind_advance = min(7 * 1440, max(0, int(remind_advance)))
    ts = now_iso()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO memos(content, created_at, updated_at, due_ts, priority,"
            " recur, remind_advance) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (content, ts, ts, due_ts, priority, recur, remind_advance),
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
    """切换完成状态。

    - 普通备忘录：标记完成时移入回收站；已完成的取消完成标记。
    - 重复任务：不进回收站，把截止时间推进到下一周期，保持待办。
    """
    now = now_iso()
    with db() as conn:
        row = conn.execute(
            "SELECT is_done, due_ts, recur FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()
        if row is None:
            return
        if row["is_done"]:
            conn.execute(
                "UPDATE memos SET is_done = 0, updated_at = ? WHERE id = ?",
                (now, memo_id),
            )
            return
        recur = row["recur"]
        if recur and row["due_ts"]:
            # 跳过所有已过去的周期，推进到下一个未来截止（加迭代上限防异常）
            nxt = int(row["due_ts"])
            now_ts = time.time()
            for _ in range(500):
                cand = next_recur_ts(nxt, recur)
                if cand is None:
                    nxt = None
                    break
                nxt = cand
                if nxt > now_ts:
                    break
            else:
                nxt = None
            if nxt is not None:
                conn.execute(
                    "UPDATE memos SET due_ts = ?, notified = 0, updated_at = ? "
                    "WHERE id = ?",
                    (nxt, now, memo_id),
                )
                return
        # 无重复或无截止时间：维持原有"完成即进回收站"行为
        conn.execute(
            "UPDATE memos SET is_done = 1, deleted_at = ?, updated_at = ? "
            "WHERE id = ?",
            (now, now, memo_id),
        )


# ---------- 截止 / 优先级 / 重复 ----------

RECUR_DAILY, RECUR_WEEKLY, RECUR_MONTHLY, RECUR_YEARLY = (
    "daily", "weekly", "monthly", "yearly",
)
RECUR_LABELS = {
    RECUR_DAILY: "每天",
    RECUR_WEEKLY: "每周",
    RECUR_MONTHLY: "每月",
    RECUR_YEARLY: "每年",
}
PRIORITY_LABELS = {0: "无", 1: "低", 2: "中", 3: "高"}


def _add_months(dt: datetime, months: int) -> datetime:
    """月份加法，日期越界时收敛到月末（1/31 + 1月 -> 2/28）。"""
    m = dt.month - 1 + months
    y = dt.year + m // 12
    m = m % 12 + 1
    # 当月天数
    if m == 12:
        nxt = datetime(y + 1, 1, 1)
    else:
        nxt = datetime(y, m + 1, 1)
    dim = (nxt - datetime(y, m, 1)).days
    return dt.replace(year=y, month=m, day=min(dt.day, dim))


def next_recur_ts(due_ts: int, recur: str) -> int | None:
    """计算下一次重复截止时间；无法识别规则返回 None。"""
    dt = datetime.fromtimestamp(due_ts)
    if recur == RECUR_DAILY:
        nxt = dt.timestamp() + 86400
    elif recur == RECUR_WEEKLY:
        nxt = dt.timestamp() + 7 * 86400
    elif recur == RECUR_MONTHLY:
        nxt = _add_months(dt, 1).timestamp()
    elif recur == RECUR_YEARLY:
        nxt = _add_months(dt, 12).timestamp()
    else:
        return None
    return int(nxt)


_UNSET = object()


def set_task_props(
    memo_id: int,
    *,
    due_ts: object = _UNSET,
    priority: object = _UNSET,
    recur: object = _UNSET,
    remind_advance: object = _UNSET,
) -> None:
    """更新任务属性；参数缺省（_UNSET）表示不修改该字段。

    due_ts 变化时重置提醒标记；recur 传 None 表示清除重复。
    """
    sets: list[str] = []
    params: list[object] = []
    with db() as conn:
        old = conn.execute(
            "SELECT due_ts, remind_advance FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()
        old_due = old["due_ts"] if old is not None else None
        old_adv = (old["remind_advance"] or 0) if old is not None else 0
        new_due = due_ts if due_ts is not _UNSET else old_due

        # 提醒时刻 = due - advance：due 或 advance 变化都要重置 notified
        adv_changed = (
            remind_advance is not _UNSET
            and new_due is not None
            and min(7 * 1440, max(0, int(remind_advance))) != old_adv
        )
        if due_ts is not _UNSET or adv_changed:
            due_changed = due_ts is not _UNSET and old_due != due_ts
            sets.append("notified = notified * ?")
            params.append(0 if (due_changed or adv_changed) else 1)
        if due_ts is not _UNSET:
            sets.append("due_ts = ?")
            params.append(due_ts)
        if priority is not _UNSET:
            sets.append("priority = ?")
            params.append(max(0, min(3, int(priority))))
        if new_due is None:
            # 不变量：recur/remind_advance 依赖 due_ts，无截止强制清除
            sets.append("recur = NULL")
            sets.append("remind_advance = 0")
        else:
            if recur is not _UNSET:
                sets.append("recur = ?")
                params.append(recur if recur in RECUR_LABELS else None)
            if remind_advance is not _UNSET:
                sets.append("remind_advance = ?")
                params.append(min(7 * 1440, max(0, int(remind_advance))))
        if not sets:
            return
        sets.append("updated_at = ?")
        params.extend((now_iso(), memo_id))
        conn.execute(
            f"UPDATE memos SET {', '.join(sets)} WHERE id = ?", params
        )


def memos_due(now_ts: int | None = None) -> list[Memo]:
    """到达提醒时间且未提醒的备忘录（含逾期）。

    提醒时刻 = due_ts - remind_advance 分钟；advance=0 即截止时刻。
    """
    now_ts = int(time.time()) if now_ts is None else now_ts
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM memos WHERE deleted_at IS NULL AND is_done = 0 "
            "AND due_ts IS NOT NULL AND due_ts - remind_advance * 60 <= ? "
            "AND notified = 0 ORDER BY due_ts - remind_advance * 60",
            (now_ts,),
        ).fetchall()
    return [Memo.from_row(r) for r in rows]


def mark_notified(memo_id: int) -> None:
    _touch(memo_id, notified=1)


def soft_delete(memo_id: int) -> None:
    _touch(memo_id, deleted_at=now_iso())


def restore(memo_id: int) -> None:
    # 恢复即回到待办状态：已完成标记一并清除，避免完成条目留在主列表
    _touch(memo_id, deleted_at=None, is_done=0)


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
    # 与快捷输入的 /分类名 语法保持一致：不允许空白和 /
    if any(ch.isspace() for ch in name) or "/" in name:
        raise ValueError("分类名不能包含空格或 /")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO categories(name, created_at) VALUES (?, ?)",
            (name, now_iso()),
        )
        return int(cur.lastrowid)


def delete_category(cat_id: int) -> None:
    """删除分类：其中的备忘录移入回收站（软删除），分类及关联一并删除。"""
    now = now_iso()
    with db() as conn:
        conn.execute(
            "UPDATE memos SET deleted_at = ?, updated_at = ? "
            "WHERE deleted_at IS NULL AND id IN "
            "(SELECT memo_id FROM memo_categories WHERE category_id = ?)",
            (now, now, cat_id),
        )
        conn.execute("DELETE FROM memo_categories WHERE category_id = ?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))


def set_category_collapsed(cat_id: int, collapsed: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE categories SET collapsed = ? WHERE id = ?",
            (1 if collapsed else 0, cat_id),
        )


def find_or_create_category(name: str) -> int:
    """按名字找分类（大小写不敏感），没有则创建，返回 id。"""
    name = name.strip()
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM categories WHERE name = ? COLLATE NOCASE", (name,)
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
