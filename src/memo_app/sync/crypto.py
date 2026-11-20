"""端到端加密（对应计划 §6）。

密钥层级：
- master_key：32B 随机库主密钥，业务批次/heads 用它 AES-256-GCM 加密；
- 用户密码 -> scrypt -> KEK(32B)，KEK 包裹 master_key 存入 vault.json；
- 每个对象独立随机 96-bit nonce，AAD 绑定 vault_id/协议版本/writer/序号，
  防止密文被挪用到其他上下文。

明文只存在于内存；落盘的一律是 nonce+密文+tag 的二进制 blob。
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PROTOCOL_VERSION = 1
KEY_LEN = 32
NONCE_LEN = 12
SALT_LEN = 16

# scrypt 参数（OWASP 2024 建议档位的保守值，防内存耗尽仍够强）
KDF_N = 2**15
KDF_R = 8
KDF_P = 1

MAX_PLAINTEXT = 8 * 1024 * 1024  # 单批次上限 8MiB，防恶意超大对象


class CryptoError(Exception):
    """加解密失败：密码错误、密文篡改/截断、格式未知。"""


def generate_master_key() -> bytes:
    return os.urandom(KEY_LEN)


def derive_key(password: str, salt: bytes) -> bytes:
    """密码 + salt -> KEK。参数固定写入 vault.json 以便日后调整。"""
    return Scrypt(
        salt=salt, length=KEY_LEN, n=KDF_N, r=KDF_R, p=KDF_P
    ).derive(password.encode("utf-8"))


def aes_gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    if len(plaintext) > MAX_PLAINTEXT:
        raise CryptoError("plaintext exceeds MAX_PLAINTEXT")
    nonce = os.urandom(NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def aes_gcm_decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < NONCE_LEN + 16:
        raise CryptoError("ciphertext too short")
    try:
        return AESGCM(key).decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], aad)
    except InvalidTag as e:
        raise CryptoError("authentication failed") from e


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def wrap_master_key(master_key: bytes, kek: bytes) -> str:
    """KEK 包裹主密钥；AAD 固定标记用途，防止与业务密文混用。"""
    return _b64e(aes_gcm_encrypt(kek, master_key, b"memo-vault-wrap-v1"))


def unwrap_master_key(wrapped_b64: str, kek: bytes) -> bytes:
    try:
        blob = _b64d(wrapped_b64)
    except Exception as e:
        raise CryptoError("invalid wrapped key encoding") from e
    return aes_gcm_decrypt(kek, blob, b"memo-vault-wrap-v1")


def seal_vault(master_key: bytes, password: str) -> dict:
    """生成 vault.json 内容：KDF 参数 + salt + 包裹后的主密钥。不含明文密钥。"""
    if not password:
        raise CryptoError("vault password must not be empty")
    salt = os.urandom(SALT_LEN)
    kek = derive_key(password, salt)
    return {
        "v": PROTOCOL_VERSION,
        "kdf": {"algo": "scrypt", "n": KDF_N, "r": KDF_R, "p": KDF_P,
                "salt": _b64e(salt)},
        "wrap": "AES-256-GCM",
        "wrapped_key": wrap_master_key(master_key, kek),
    }


def open_vault(config: dict, password: str) -> bytes:
    """用密码解开 vault.json，返回主密钥。任何一步失败抛 CryptoError。"""
    try:
        v = config.get("v")
        if not (isinstance(v, int) and not isinstance(v, bool)
                and v == PROTOCOL_VERSION):
            raise CryptoError(f"unsupported vault version: {v}")
        kdf = config["kdf"]
        if kdf.get("algo") != "scrypt":
            raise CryptoError(f"unsupported kdf: {kdf.get('algo')}")
        # KDF 参数来自远端配置=不可信输入：必须严格有界。
        # scrypt 内存 ≈ 128*n*r 字节且 OpenSSL 后端无有效 maxmem，
        # 因此除范围外还要校验 n 为 2 的幂并限制总内存。
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        if (
            (n & (n - 1)) != 0
            or not (2**10 <= n <= 2**17)
            or not (1 <= r <= 16)
            or not (1 <= p <= 4)
            or 128 * n * r > 256 * 1024 * 1024
        ):
            raise CryptoError("KDF parameters out of bounds")
        salt_b64 = kdf["salt"]
        if not isinstance(salt_b64, str) or len(salt_b64) > 88:
            raise CryptoError("invalid KDF salt encoding")
        salt = _b64d(salt_b64)
        if not (8 <= len(salt) <= 64):
            raise CryptoError("invalid KDF salt length")
        kek = Scrypt(salt=salt, length=KEY_LEN, n=n, r=r, p=p).derive(
            password.encode("utf-8")
        )
        return unwrap_master_key(config["wrapped_key"], kek)
    except CryptoError:
        raise
    except Exception as e:
        raise CryptoError("cannot open vault") from e


def vault_aad(vault_id: str) -> bytes:
    return f"memo-vault:{PROTOCOL_VERSION}:{vault_id}".encode("utf-8")


def batch_aad(vault_id: str, writer_id: str, seq: int) -> bytes:
    return (
        f"memo-batch:{PROTOCOL_VERSION}:{vault_id}:{writer_id}:{seq}"
    ).encode("utf-8")


def head_aad(vault_id: str, writer_id: str) -> bytes:
    return f"memo-head:{PROTOCOL_VERSION}:{vault_id}:{writer_id}".encode("utf-8")


def blob_aad(vault_id: str, name: str) -> bytes:
    """图片 blob：绑定 vault 与文件名，防止跨 vault / 换名重放。"""
    return f"memo-blob:{PROTOCOL_VERSION}:{vault_id}:{name}".encode("utf-8")


def encrypt_json(key: bytes, obj: dict, aad: bytes) -> bytes:
    return aes_gcm_encrypt(
        key, json.dumps(obj, ensure_ascii=False).encode("utf-8"), aad
    )


def decrypt_json(key: bytes, blob: bytes, aad: bytes) -> dict:
    try:
        obj = json.loads(aes_gcm_decrypt(key, blob, aad))
    except CryptoError:
        raise
    except Exception as e:
        raise CryptoError("decrypted payload is not valid JSON") from e
    if not isinstance(obj, dict):
        raise CryptoError("decrypted payload is not an object")
    return obj
