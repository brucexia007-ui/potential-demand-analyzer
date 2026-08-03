"""API Key 加密 / 解密 / 脱敏服务

使用 Fernet 对称加密（AES-128-CBC + HMAC-SHA256），密钥从环境变量
CONFIG_ENCRYPTION_KEY 读取。未配置时抛出明确错误，不使用硬编码密钥。
"""
import os
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_ENV_KEY_NAME = "CONFIG_ENCRYPTION_KEY"


class EncryptionKeyNotConfiguredError(RuntimeError):
    """CONFIG_ENCRYPTION_KEY 环境变量未设置时抛出"""

    def __init__(self) -> None:
        super().__init__(
            f"环境变量 {_ENV_KEY_NAME} 未配置。"
            f"请运行以下命令生成密钥并设置：\n"
            f"  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            f"然后将输出的密钥设置为 {_ENV_KEY_NAME} 环境变量。"
        )


def _get_fernet() -> Fernet:
    """获取 Fernet 实例，如果密钥未配置则抛出明确错误"""
    key = os.getenv(_ENV_KEY_NAME)
    if not key:
        raise EncryptionKeyNotConfiguredError()
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as e:
        raise ValueError(
            f"环境变量 {_ENV_KEY_NAME} 的值不是有效的 Fernet 密钥。"
            f"请使用 cryptography.fernet.Fernet.generate_key() 生成。"
        ) from e


def encrypt_secret(secret: str | None) -> str | None:
    """加密敏感字符串。

    Args:
        secret: 明文字符串，None 或空字符串将返回原值

    Returns:
        Fernet token（base64 编码的加密值），或 None
    """
    if not secret:
        return None
    f = _get_fernet()
    return f.encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted: str | None) -> str | None:
    """解密密文字符串。

    Args:
        encrypted: Fernet token 或 None

    Returns:
        明文字符串，或 None
    """
    if not encrypted:
        return None
    f = _get_fernet()
    try:
        return f.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        logger.error("解密失败：密文可能已损坏或加密密钥不匹配")
        raise ValueError("解密失败：密文无效或密钥不匹配") from e


def mask_secret(secret: str | None) -> str | None:
    """脱敏显示 API Key。

    Args:
        secret: 原始 API Key 字符串或 None

    Returns:
        - None → None
        - 空字符串 → None
        - 长度 ≤ 8 → "****"
        - 长度 > 8 → "前4位****后4位"（如 sk-1****cdef）
    """
    if not secret:
        return None
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}****{secret[-4:]}"
