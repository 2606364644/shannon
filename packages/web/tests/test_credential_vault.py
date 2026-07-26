"""P3c 阶段 2：CredentialVault Fernet 加解密。"""
import os
from pathlib import Path
import pytest

from supernova_web.components.credential_vault import CredentialVault


def test_encrypt_decrypt_roundtrip(tmp_path):
    vault = CredentialVault(tmp_path / ".master_key")
    cipher = vault.encrypt("sk-secret-123")
    assert cipher != "sk-secret-123"          # 密文非明文
    assert vault.decrypt(cipher) == "sk-secret-123"


def test_encrypt_none_is_none(tmp_path):
    vault = CredentialVault(tmp_path / ".master_key")
    assert vault.encrypt(None) is None
    assert vault.decrypt(None) is None


def test_first_run_generates_master_key_file(tmp_path):
    key_file = tmp_path / ".master_key"
    assert not key_file.exists()
    CredentialVault(key_file)
    assert key_file.exists()
    # 0600 权限（非 Windows）
    if os.name == "posix":
        assert oct(key_file.stat().st_mode)[-3:] == "600"


def test_env_master_key_takes_priority(monkeypatch, tmp_path):
    """SUPERNOVA_MASTER_KEY env 优先于文件。"""
    from cryptography.fernet import Fernet
    env_key = Fernet.generate_key().decode()
    monkeypatch.setenv("SUPERNOVA_MASTER_KEY", env_key)
    file_key = Fernet.generate_key()
    (tmp_path / ".master_key").write_bytes(file_key)
    vault = CredentialVault(tmp_path / ".master_key")
    # 用 env key 加密，用另一个 vault（同 env key）能解
    cipher = vault.encrypt("x")
    other = CredentialVault(tmp_path / "other.key")  # 也读 env
    assert other.decrypt(cipher) == "x"


def test_decrypt_invalid_token_returns_none(tmp_path, caplog):
    """密文损坏/master key 不匹配 → None + warning，不崩。"""
    vault = CredentialVault(tmp_path / ".master_key")
    assert vault.decrypt("not-a-valid-fernet-token") is None


def test_existing_master_key_reused(tmp_path):
    """二次启动复用已生成的 key（不重新生成）。"""
    key_file = tmp_path / ".master_key"
    v1 = CredentialVault(key_file)
    cipher = v1.encrypt("persist")
    v2 = CredentialVault(key_file)  # 复用
    assert v2.decrypt(cipher) == "persist"


def test_credential_fields_constant():
    """凭据白名单常量（防漏加密）。"""
    assert "api_key" in CredentialVault.CREDENTIAL_FIELDS
