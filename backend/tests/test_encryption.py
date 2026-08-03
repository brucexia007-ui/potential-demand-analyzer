"""WBS-1.2 ConfigEncryptionService 测试"""
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.config_center.encryption import (
    encrypt_secret,
    decrypt_secret,
    mask_secret,
    EncryptionKeyNotConfiguredError,
)

# 预生成一个固定测试密钥，避免每次测试生成
_TEST_KEY = Fernet.generate_key().decode()


# ── 加密 / 解密往返测试 ──────────────────────────────────────────────

class TestEncryptDecrypt:
    """验证加密解密的功能正确性"""

    @pytest.fixture(autouse=True)
    def _set_key(self):
        """每个测试注入测试密钥"""
        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
            yield

    def test_roundtrip_normal_key(self):
        plain = "sk-1234567890abcdef"
        encrypted = encrypt_secret(plain)
        assert encrypted is not None
        assert encrypted != plain
        assert plain not in encrypted
        decrypted = decrypt_secret(encrypted)
        assert decrypted == plain

    def test_roundtrip_special_characters(self):
        plain = "sk-!@#$%^&*()_+-=[]{}|;':\",./<>?"
        encrypted = encrypt_secret(plain)
        assert encrypted is not None
        assert decrypt_secret(encrypted) == plain

    def test_roundtrip_chinese_characters(self):
        plain = "密钥-测试-中文"
        encrypted = encrypt_secret(plain)
        assert decrypt_secret(encrypted) == plain

    def test_encrypt_none_returns_none(self):
        assert encrypt_secret(None) is None

    def test_encrypt_empty_string_returns_none(self):
        assert encrypt_secret("") is None

    def test_decrypt_none_returns_none(self):
        assert decrypt_secret(None) is None

    def test_decrypt_empty_string_returns_none(self):
        assert decrypt_secret("") is None

    def test_different_plaintexts_produce_different_ciphertexts(self):
        """相同明文两次加密结果不同（Fernet 包含随机 IV）"""
        plain = "sk-test-key-1234"
        enc1 = encrypt_secret(plain)
        enc2 = encrypt_secret(plain)
        assert enc1 != enc2
        # 但都能正确解密
        assert decrypt_secret(enc1) == plain
        assert decrypt_secret(enc2) == plain


# ── mask_secret 测试 ────────────────────────────────────────────────

class TestMaskSecret:
    """验证脱敏规则"""

    def test_mask_none_returns_none(self):
        assert mask_secret(None) is None

    def test_mask_empty_string_returns_none(self):
        assert mask_secret("") is None

    def test_mask_short_key(self):
        """长度 ≤ 8 全部替换为 ****"""
        assert mask_secret("abc") == "****"
        assert mask_secret("12345678") == "****"
        assert mask_secret("sk-1234") == "****"

    def test_mask_long_key(self):
        """长度 > 8：前4位****后4位"""
        assert mask_secret("sk-1234567890abcdef") == "sk-1****cdef"
        assert mask_secret("abcdefghijklmn") == "abcd****klmn"

    def test_mask_standard_openai_key_format(self):
        """OpenAI 格式 sk-xxx..."""
        result = mask_secret("sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")
        assert result == "sk-p****3456"
        # 确保完整 key 不在脱敏结果中
        assert "ABCDEFGHIJKLMNOP" not in result


# ── 未配置密钥的错误行为 ─────────────────────────────────────────────

class TestMissingEncryptionKey:
    """验证 CONFIG_ENCRYPTION_KEY 未设置时的错误行为"""

    @pytest.fixture(autouse=True)
    def _clear_key(self):
        """确保测试期间没有设置密钥"""
        with patch.dict(os.environ, {}, clear=True):
            # clear=True 清空所有环境变量，只保留 patch 提供的空 dict
            yield

    def test_encrypt_raises_when_key_missing(self):
        with pytest.raises(EncryptionKeyNotConfiguredError) as exc_info:
            encrypt_secret("some-secret")
        assert "CONFIG_ENCRYPTION_KEY" in str(exc_info.value)

    def test_decrypt_raises_when_key_missing(self):
        with pytest.raises(EncryptionKeyNotConfiguredError) as exc_info:
            decrypt_secret("some-encrypted-token")
        assert "CONFIG_ENCRYPTION_KEY" in str(exc_info.value)

    def test_encrypt_none_does_not_raise_when_key_missing(self):
        """None 输入不需要加密密钥，不应抛错"""
        result = encrypt_secret(None)
        assert result is None

    def test_decrypt_none_does_not_raise_when_key_missing(self):
        result = decrypt_secret(None)
        assert result is None


# ── 无效密钥的错误行为 ─────────────────────────────────────────────

class TestInvalidEncryptionKey:
    """验证配置了无效密钥时的错误行为"""

    def test_invalid_key_format_raises(self):
        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": "not-a-valid-fernet-key"}):
            with pytest.raises(ValueError) as exc_info:
                encrypt_secret("test")
            assert "CONFIG_ENCRYPTION_KEY" in str(exc_info.value)

    def test_decrypt_with_wrong_key_raises(self):
        """用不同的密钥解密——密文无效"""
        key_a = Fernet.generate_key().decode()
        key_b = Fernet.generate_key().decode()

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": key_a}):
            encrypted = encrypt_secret("my-secret")

        with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": key_b}):
            with pytest.raises(ValueError) as exc_info:
                decrypt_secret(encrypted)
            assert "解密失败" in str(exc_info.value)
