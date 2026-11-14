"""crypto + protocol 单元测试。运行：python -m unittest discover -s tests"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memo_app.sync import crypto, protocol  # noqa: E402


class CryptoTest(unittest.TestCase):
    def test_vault_roundtrip(self) -> None:
        mk = crypto.generate_master_key()
        cfg = crypto.seal_vault(mk, "correct horse battery")
        # vault.json 不含明文主密钥
        self.assertNotIn(mk.hex(), str(cfg))
        self.assertEqual(crypto.open_vault(cfg, "correct horse battery"), mk)

    def test_wrong_password_rejected(self) -> None:
        mk = crypto.generate_master_key()
        cfg = crypto.seal_vault(mk, "right")
        with self.assertRaises(crypto.CryptoError):
            crypto.open_vault(cfg, "wrong")

    def test_tampered_wrapped_key_rejected(self) -> None:
        mk = crypto.generate_master_key()
        cfg = crypto.seal_vault(mk, "pw")
        cfg["wrapped_key"] = cfg["wrapped_key"][:-4] + "AAAA"
        with self.assertRaises(crypto.CryptoError):
            crypto.open_vault(cfg, "pw")

    def test_untrusted_kdf_params_bounded(self) -> None:
        mk = crypto.generate_master_key()
        cfg = crypto.seal_vault(mk, "pw")
        cfg["kdf"]["n"] = 2**40  # 恶意超大参数必须被拒
        with self.assertRaises(crypto.CryptoError):
            crypto.open_vault(cfg, "pw")

    def test_aead_context_binding(self) -> None:
        key = crypto.generate_master_key()
        aad1 = crypto.batch_aad("v" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12, "w" * 36, 1)
        blob = crypto.aes_gcm_encrypt(key, b"hello", aad1)
        # 换 seq 解密必须失败（AAD 绑定）
        aad2 = crypto.batch_aad(
            "v" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12,
            "w" * 36, 2)
        with self.assertRaises(crypto.CryptoError):
            crypto.aes_gcm_decrypt(key, blob, aad2)

    def test_truncated_ciphertext_rejected(self) -> None:
        key = crypto.generate_master_key()
        blob = crypto.aes_gcm_encrypt(key, b"data", b"aad")
        with self.assertRaises(crypto.CryptoError):
            crypto.aes_gcm_decrypt(key, blob[:10], b"aad")

    def test_nonce_unique_per_encryption(self) -> None:
        key = crypto.generate_master_key()
        b1 = crypto.aes_gcm_encrypt(key, b"same", b"aad")
        b2 = crypto.aes_gcm_encrypt(key, b"same", b"aad")
        self.assertNotEqual(b1, b2)  # 随机 nonce -> 密文不同

    def test_encrypt_json_roundtrip_and_aad(self) -> None:
        key = crypto.generate_master_key()
        aad = crypto.batch_aad("v" * 36, "w" * 36, 1)
        blob = crypto.encrypt_json(key, {"a": 1}, aad)
        self.assertEqual(crypto.decrypt_json(key, blob, aad), {"a": 1})
        with self.assertRaises(crypto.CryptoError):
            crypto.decrypt_json(key, blob, crypto.batch_aad("v" * 36, "w" * 36, 2))

    def test_open_vault_malformed_configs(self) -> None:
        mk = crypto.generate_master_key()
        cfg = crypto.seal_vault(mk, "pw")
        for mutate in (
            lambda c: c.update({"v": 99}),
            lambda c: c["kdf"].update({"algo": "pbkdf2"}),
            lambda c: c["kdf"].update({"n": 7}),          # 非 2 的幂
            lambda c: c["kdf"].update({"n": 2**30}),      # 超内存上限
            lambda c: c["kdf"].update({"salt": "!!"}),    # 非法 b64
            lambda c: c.update({"wrapped_key": "!!!"}),
            lambda c: c.pop("wrapped_key"),
        ):
            c = json.loads(json.dumps(cfg))
            mutate(c)
            with self.assertRaises(crypto.CryptoError, msg=str(mutate)):
                crypto.open_vault(c, "pw")


class ProtocolTest(unittest.TestCase):
    VID = "12345678-1234-1234-1234-123456789abc"
    WID = "abcdefab-cdef-abcd-efab-cdefabcdefab"
    EID = "11111111-2222-3333-4444-555555555555"

    def _op(self) -> dict:
        return {
            "v": 1, "entity": "memo", "entity_uuid": self.EID,
            "op": "upsert", "rev_ts": 1700000000.0, "writer_id": self.WID,
            "data": {"content": "hi"},
        }

    def test_batch_roundtrip_validate(self) -> None:
        b = protocol.build_batch(self.WID, 3, [self._op()])
        protocol.validate_batch(b)
        self.assertEqual(b["seq"], 3)
        self.assertTrue(protocol.is_uuid(b["batch_id"]))

    def test_validate_rejects_bad(self) -> None:
        bad = self._op()
        bad["entity"] = "unknown"
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_op(bad)
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_batch("not-a-uuid", 1, [self._op()])
        with self.assertRaises(protocol.ProtocolError):
            protocol.commit_path(self.VID, self.WID, 0)

    def test_paths(self) -> None:
        self.assertEqual(
            protocol.commit_path(self.VID, self.WID, 7),
            f"memo-sync/{self.VID}/commits/{self.WID}/7.enc",
        )
        self.assertEqual(
            protocol.head_path(self.VID, self.WID),
            f"memo-sync/{self.VID}/heads/{self.WID}.enc",
        )

    def test_href_parsing(self) -> None:
        base = protocol.vault_id_path(self.VID)
        href = f"https://dav.jianguoyun.com/dav/{base}/heads/{self.WID}.enc"
        self.assertEqual(protocol.parse_remote_href(href, base), f"heads/{self.WID}.enc")
        # 越界与穿越必须拒绝
        self.assertIsNone(protocol.parse_remote_href(
            f"https://dav.jianguoyun.com/dav/memo-sync/{self.VID}x/heads/a.enc", base))
        self.assertIsNone(protocol.parse_remote_href(
            f"https://dav.jianguoyun.com/dav/{base}/../evil.enc", base))

    def test_head_validate(self) -> None:
        h = protocol.build_head(self.WID, 9)
        protocol.validate_head(h)
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_head({"v": 1, "writer_id": self.WID, "seq": 0})

    def test_batch_id_and_writer_consistency(self) -> None:
        b = protocol.build_batch(self.WID, 1, [self._op()])
        del b["batch_id"]
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_batch(b)
        # op.writer_id 与批次 writer_id 不一致必须拒绝
        other = "99999999-8888-7777-6666-555555555555"
        b2 = protocol.build_batch(self.WID, 1, [self._op()])
        b2["ops"][0]["writer_id"] = other
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_batch(b2)

    def test_bool_and_nan_rejected(self) -> None:
        # bool 不是合法 seq；NaN 不是合法 rev_ts
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_batch(self.WID, True, [self._op()])
        op = self._op()
        op["rev_ts"] = float("nan")
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_op(op)
        b = protocol.build_batch(self.WID, 1, [self._op()])
        b["seq"] = True
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_batch(b)

    def test_batch_size_limit_enforced_on_build(self) -> None:
        # 超大批次在 build 阶段就拒绝，避免产生对端无法接受的死锁批次
        big = "x" * (protocol.MAX_BATCH_PLAINTEXT)
        ops = [{**self._op(), "data": {"content": big}}]
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_batch(self.WID, 1, ops)

    def test_href_backslash_and_encoded_traversal(self) -> None:
        base = protocol.vault_id_path(self.VID)
        # %5c 解码为反斜杠、%2e%2e 解码为 ..，都必须拒绝
        self.assertIsNone(protocol.parse_remote_href(
            f"https://dav.jianguoyun.com/dav/{base}/heads/%2e%2e/x.enc", base))
        self.assertIsNone(protocol.parse_remote_href(
            f"https://dav.jianguoyun.com/dav/{base}/heads/a%5cb.enc", base))

    def test_huge_int_rev_ts_rejected(self) -> None:
        # json 可携带任意大整数字面量；isfinite 会 OverflowError，必须兜底
        op = self._op()
        op["rev_ts"] = 10**400
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_op(op)

    def test_version_alias_rejected(self) -> None:
        # True == 1、1.0 == 1 的别名漏洞：v 必须是严格 int 1
        for bad_v in (True, 1.0, "1"):
            op = self._op()
            op["v"] = bad_v
            with self.assertRaises(protocol.ProtocolError, msg=repr(bad_v)):
                protocol.validate_op(op)
            b = protocol.build_batch(self.WID, 1, [self._op()])
            b["v"] = bad_v
            with self.assertRaises(protocol.ProtocolError, msg=repr(bad_v)):
                protocol.validate_batch(b)


if __name__ == "__main__":
    unittest.main()
