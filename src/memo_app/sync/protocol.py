"""同步协议 v1：远端布局、批次信封、校验。纯数据/校验，不依赖网络与 GUI。

远端布局（计划 §4.2）：
    memo-sync/<vault_id>/vault.json
    memo-sync/<vault_id>/heads/<writer_id>.enc
    memo-sync/<vault_id>/commits/<writer_id>/<seq>.enc

批次信封（加密前的 JSON）：
    {"v":1,"batch_id","writer_id","seq","created_utc","ops":[...]}
ops 元素即 sync_outbox.payload 解析出的修订信封
    {"v","entity","entity_uuid","op","rev_ts","writer_id","data"}。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid as uuidlib
from datetime import datetime, timezone

from . import crypto

PROTOCOL_VERSION = crypto.PROTOCOL_VERSION
REMOTE_ROOT = "memo-sync"
VAULT_FILE = "vault.json"
HEADS_DIR = "heads"
COMMITS_DIR = "commits"

MAX_OPS_PER_BATCH = 500
MAX_BATCH_PLAINTEXT = 4 * 1024 * 1024  # 加密前 JSON 上限
MAX_REMOTE_FILE = 16 * 1024 * 1024     # GET 响应体上限（防恶意大文件）

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ENTITIES = {"memo", "category"}
_OPS = {"upsert", "delete"}


class ProtocolError(Exception):
    """远端数据不符合协议 v1：版本未知、结构非法、超限。"""


def is_uuid(s: object) -> bool:
    return isinstance(s, str) and bool(_UUID_RE.match(s))


def _is_int(x: object) -> bool:
    """严格 int：拒绝 bool（isinstance(True, int) 陷阱）。"""
    return isinstance(x, int) and not isinstance(x, bool)


def _is_finite_num(x: object) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(x)
    except OverflowError:
        # 超出 float 范围的巨型 int：json 可携带，isfinite 会抛异常
        return False


def _is_version(x: object) -> bool:
    """版本字段严格校验：True==1 / 1.0==1 的别名漏洞要堵住。"""
    return _is_int(x) and x == PROTOCOL_VERSION


def vault_id_path(vault_id: str) -> str:
    if not is_uuid(vault_id):
        raise ProtocolError("invalid vault_id")
    return f"{REMOTE_ROOT}/{vault_id}"


def vault_json_path(vault_id: str) -> str:
    return f"{vault_id_path(vault_id)}/{VAULT_FILE}"


def head_path(vault_id: str, writer_id: str) -> str:
    if not is_uuid(writer_id):
        raise ProtocolError("invalid writer_id")
    return f"{vault_id_path(vault_id)}/{HEADS_DIR}/{writer_id}.enc"


def commit_path(vault_id: str, writer_id: str, seq: int) -> str:
    if not is_uuid(writer_id):
        raise ProtocolError("invalid writer_id")
    if not _is_int(seq) or seq < 1:
        raise ProtocolError("invalid commit seq")
    return f"{vault_id_path(vault_id)}/{COMMITS_DIR}/{writer_id}/{seq}.enc"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_batch(writer_id: str, seq: int, ops: list[dict]) -> dict:
    """把 outbox 修订信封打包成批次信封。

    序列化大小必须 <= MAX_BATCH_PLAINTEXT：否则本端能加密上传、
    所有对端 validate_batch 却拒收，按序应用的日志链将永久死锁。
    """
    if not is_uuid(writer_id):
        raise ProtocolError("invalid writer_id")
    if not _is_int(seq) or seq < 1:
        raise ProtocolError("invalid seq")
    if not ops or len(ops) > MAX_OPS_PER_BATCH:
        raise ProtocolError("invalid ops count")
    batch = {
        "v": PROTOCOL_VERSION,
        "batch_id": str(uuidlib.uuid4()),
        "writer_id": writer_id,
        "seq": seq,
        "created_utc": utc_now_iso(),
        "ops": ops,
    }
    size = len(json.dumps(batch, ensure_ascii=False).encode("utf-8"))
    if size > MAX_BATCH_PLAINTEXT:
        raise ProtocolError("batch too large")
    # 自检：本地构造的批次必须能通过接收端同一套校验，
    # 防止畸形 op 打包后在对端被拒造成日志链死锁
    validate_batch(batch)
    return batch


def validate_op(op: object) -> dict:
    """校验单条修订信封（远端不可信输入）。返回原 dict。"""
    if not isinstance(op, dict):
        raise ProtocolError("op is not an object")
    if not _is_version(op.get("v")):
        raise ProtocolError(f"unsupported op version: {op.get('v')}")
    if op.get("entity") not in _ENTITIES:
        raise ProtocolError(f"unknown entity: {op.get('entity')}")
    if op.get("op") not in _OPS:
        raise ProtocolError(f"unknown op: {op.get('op')}")
    if not is_uuid(op.get("entity_uuid")):
        raise ProtocolError("invalid entity_uuid")
    if not _is_finite_num(op.get("rev_ts")):
        raise ProtocolError("invalid rev_ts")
    if not is_uuid(op.get("writer_id")):
        raise ProtocolError("invalid op writer_id")
    if op["op"] == "upsert" and not isinstance(op.get("data"), dict):
        raise ProtocolError("upsert requires data object")
    return op


def validate_batch(obj: object) -> dict:
    """校验解密后的批次信封。返回原 dict。"""
    if not isinstance(obj, dict):
        raise ProtocolError("batch is not an object")
    if not _is_version(obj.get("v")):
        raise ProtocolError(f"unsupported batch version: {obj.get('v')}")
    if not is_uuid(obj.get("batch_id")):
        raise ProtocolError("invalid batch_id")
    if not is_uuid(obj.get("writer_id")):
        raise ProtocolError("invalid batch writer_id")
    if not isinstance(obj.get("created_utc"), str):
        raise ProtocolError("invalid created_utc")
    if not _is_int(obj.get("seq")) or obj["seq"] < 1:
        raise ProtocolError("invalid batch seq")
    ops = obj.get("ops")
    if not isinstance(ops, list) or not ops or len(ops) > MAX_OPS_PER_BATCH:
        raise ProtocolError("invalid ops")
    for op in ops:
        validate_op(op)
        # 每个 writer 只发自己的日志：op 归属必须与批次一致
        if op["writer_id"] != obj["writer_id"]:
            raise ProtocolError("op writer_id mismatch")
    size = len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    if size > MAX_BATCH_PLAINTEXT:
        raise ProtocolError("batch too large")
    return obj


def batch_digest(batch: dict) -> str:
    """批次内容摘要（sha256 hex），用于同路径重试时校验是否同一批次。"""
    raw = json.dumps(batch, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_head(writer_id: str, seq: int) -> dict:
    if not is_uuid(writer_id) or not _is_int(seq) or seq < 1:
        raise ProtocolError("invalid head")
    return {"v": PROTOCOL_VERSION, "writer_id": writer_id, "seq": seq}


def validate_head(obj: object) -> dict:
    if not isinstance(obj, dict) or not _is_version(obj.get("v")):
        raise ProtocolError("invalid head")
    if not is_uuid(obj.get("writer_id")):
        raise ProtocolError("invalid head writer_id")
    if not _is_int(obj.get("seq")) or obj["seq"] < 1:
        raise ProtocolError("invalid head seq")
    return obj


def parse_remote_href(href: str, base_path: str) -> str | None:
    """把 PROPFIND 返回的 href 解析为相对 vault 的路径；越界返回 None。"""
    # href 可能是绝对 URL 或绝对路径；只取 path 部分
    from urllib.parse import unquote, urlsplit

    path = unquote(urlsplit(href).path)
    # WebDAV href 以 /dav/ 前缀开头（坚果云），去掉前缀后应落在 base_path 下
    idx = path.find("/dav/")
    if idx >= 0:
        path = path[idx + 4:]
    path = path.lstrip("/")
    # 必须是 base_path 本身或其子路径（"<vid>x" 这类前缀不算）
    if path != base_path and not path.startswith(base_path + "/"):
        return None
    rel = path[len(base_path):].lstrip("/")
    # 拒绝路径穿越与 Windows 不安全字符（防 %5c 解码后变反斜杠等）
    if ".." in rel.split("/") or "\\" in rel or "\x00" in rel:
        return None
    if any(ord(ch) < 0x20 for ch in rel):
        return None
    return rel or None
