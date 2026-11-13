"""凭据安全存储（计划 §6）：账号/应用密码/库密码存 Windows Credential Manager。

keyring 不可用时拒绝明文持久化——调用方应提示"仅本次会话输入"。
本模块不读不写任何配置文件，秘密不进日志与 QSettings。
"""
from __future__ import annotations

SERVICE = "memo-app-sync"
KEY_ACCOUNT = "jianguoyun_account"
KEY_APP_PASSWORD = "jianguoyun_app_password"
KEY_VAULT_PASSWORD = "vault_password"


class CredentialError(Exception):
    """安全存储不可用或操作失败。"""


def is_available() -> bool:
    """探测安全存储真实可用（写/读/删探针），而非只看 backend 对象。"""
    probe = "__memo_app_probe__"
    try:
        import keyring

        keyring.set_password(SERVICE, probe, "1")
        ok = keyring.get_password(SERVICE, probe) == "1"
        keyring.delete_password(SERVICE, probe)
        return ok
    except Exception:
        return False


def _set(key: str, value: str) -> None:
    try:
        import keyring

        keyring.set_password(SERVICE, key, value)
    except Exception as e:
        raise CredentialError("写入系统安全存储失败") from e


def _get(key: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, key)
    except Exception as e:
        raise CredentialError("读取系统安全存储失败") from e


def _delete(key: str) -> None:
    try:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, key)
        except PasswordDeleteError:
            pass
    except CredentialError:
        raise
    except Exception as e:
        raise CredentialError("删除系统安全存储条目失败") from e


def save_credentials(account: str, app_password: str) -> None:
    _set(KEY_ACCOUNT, account)
    _set(KEY_APP_PASSWORD, app_password)


def load_credentials() -> tuple[str | None, str | None]:
    return _get(KEY_ACCOUNT), _get(KEY_APP_PASSWORD)


def clear_credentials() -> None:
    _delete(KEY_ACCOUNT)
    _delete(KEY_APP_PASSWORD)


def save_vault_password(password: str) -> None:
    _set(KEY_VAULT_PASSWORD, password)


def load_vault_password() -> str | None:
    return _get(KEY_VAULT_PASSWORD)


def clear_vault_password() -> None:
    _delete(KEY_VAULT_PASSWORD)
