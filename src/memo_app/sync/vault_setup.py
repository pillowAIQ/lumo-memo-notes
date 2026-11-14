"""vault 初始化/加入（计划 §4.2 vault.json）。

纯逻辑模块，不依赖 Qt：service/测试均可直接调用。
- create_vault：生成主密钥 -> seal -> PUT vault.json（create_only）；
- open_remote_vault：GET vault.json -> open_vault 解主密钥。

vault.json 本身不加密（内含 scrypt 包裹的主密钥），
但经 WebDAV Basic Auth 传输且内容不可被伪造利用——
篡改会导致 open_vault 失败而非静默接受。
"""
from __future__ import annotations

import json

from . import crypto, protocol
from .transport import (
    NotFoundError,
    WebDAVClient,
    WebDAVError,
)


def _layout(client: WebDAVClient, writer_id: str) -> None:
    client.mkcol_root()
    client.mkcol("")                                   # vault 根
    client.mkcol(protocol.HEADS_DIR)
    client.mkcol(protocol.COMMITS_DIR)
    client.mkcol(f"{protocol.COMMITS_DIR}/{writer_id}")


def create_vault(
    client: WebDAVClient,
    vault_id: str,
    writer_id: str,
    password: str,
) -> bytes:
    """创建新 vault：返回 master_key。vault.json 已存在则失败。

    vault_id 内嵌进 vault.json——加入端读配置即获得身份，
    不再需要用户手输库 ID（Obsidian 式：选同一远程目录即可加入）。
    """
    master_key = crypto.generate_master_key()
    config = crypto.seal_vault(master_key, password)
    config["vault_id"] = vault_id
    blob = json.dumps(config, ensure_ascii=False).encode("utf-8")
    _layout(client, writer_id)
    # 预检 + 写后回读双保险：部分服务端不支持 If-None-Match，
    # 不能依赖 412；回读验证保证 vault.json 与本次写入一致
    if client.exists(protocol.VAULT_FILE):
        raise WebDAVError("vault already exists")
    client.put(protocol.VAULT_FILE, blob, create_only=True)
    got, _ = open_remote_vault(client, password)
    if got != master_key:
        raise crypto.CryptoError("vault.json verification failed")
    return master_key


def fetch_vault_config(client: WebDAVClient) -> dict:
    """GET vault.json 并解析为 dict（远端不可信输入做基本结构检查）。"""
    try:
        blob = client.get(protocol.VAULT_FILE)
    except NotFoundError as e:
        raise WebDAVError("vault.json not found") from e
    try:
        obj = json.loads(blob)
    except (ValueError, UnicodeDecodeError) as e:
        raise WebDAVError("vault.json is not valid JSON") from e
    if not isinstance(obj, dict):
        raise WebDAVError("vault.json is not an object")
    return obj


def open_remote_vault(
    client: WebDAVClient, password: str
) -> tuple[bytes, dict]:
    """读取远端 vault.json 并用密码解出主密钥。返回 (master_key, config)。"""
    config = fetch_vault_config(client)
    master_key = crypto.open_vault(config, password)
    return master_key, config
