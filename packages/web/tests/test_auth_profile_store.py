"""AuthProfileStore:读写 + 显式路径加密/解密 + 嵌套 email_login + 脱敏 + 空串保留。"""
from pathlib import Path

import yaml

from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, EmailLoginCred,
    credential_to_authentication,
)


def _vault(tmp_path):
    return CredentialVault(tmp_path / ".master_key")


def _profile():
    return AuthProfile(
        id="prof_1", name="NodeGoat", login_url="http://t/", login_type="form",
        login_flow=["打开登录页", "成功标志:URL 含 /dashboard"],
        credentials=[
            AuthProfileCredential(id="cred_a", role="admin", username="admin",
                                  password="pw", email_login=EmailLoginCred(
                                      address="a@x.com", password="epw", totp_secret="et")),
            AuthProfileCredential(id="cred_b", role="user", username="u1", password=None),
        ],
    )


def test_roundtrip_decrypts_all_sensitive_paths(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])
    # 落盘密文(非明文):确定性校验,不做 raw 子串反断(Fernet base64 密文会随机出现
    # "pw"/"et" 等短子串,导致测试 spuriously flaky)。
    raw = (tmp_path / "ws1" / "auth-profiles.yaml").read_text("utf-8")
    parsed = yaml.safe_load(raw)
    cred0 = parsed[0]["credentials"][0]
    cred1 = parsed[0]["credentials"][1]
    el0 = cred0["email_login"]
    # Fernet token 总以 "gAAAAA" 起首
    assert cred0["password"].startswith("gAAAAA") and cred0["password"] != "pw"
    assert cred0["totp_secret"] is None  # 源为 None → 加密后仍 None
    assert el0["password"].startswith("gAAAAA") and el0["password"] != "epw"
    assert el0["totp_secret"].startswith("gAAAAA") and el0["totp_secret"] != "et"
    assert cred1["password"] is None      # 源为 None → 加密后仍 None
    # 读回解密还原
    loaded = store.read("ws1")
    assert loaded[0].credentials[0].password == "pw"
    assert loaded[0].credentials[0].email_login.password == "epw"
    assert loaded[0].credentials[0].email_login.totp_secret == "et"


def test_mask_for_get_returns_masked_or_none(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])
    masked = store.read_masked("ws1")
    cred = masked[0].credentials[0]
    assert cred.password == "••••"          # 有值 → 掩码
    assert masked[0].credentials[1].password is None  # 无值 → None


def test_apply_update_empty_secret_keeps_existing(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])
    # PUT 空串 password + totp_secret = 不改(保留原密文)
    store.apply_update("ws1", "prof_1", "cred_a",
                       username="admin2", password="", totp_secret="")
    cred = store.read("ws1")[0].credentials[0]
    assert cred.username == "admin2"          # 非敏感字段已更新
    assert cred.password == "pw"              # 原密文保留


def test_credential_to_authentication_aligns_core_schema(tmp_path):
    auth = credential_to_authentication(_profile(), _profile().credentials[0])
    assert auth.login_type == "form"
    assert auth.login_url == "http://t/"
    assert auth.credentials.username == "admin"
    assert auth.credentials.password == "pw"
    assert auth.credentials.email_login.address == "a@x.com"
    assert auth.login_flow == ["打开登录页", "成功标志:URL 含 /dashboard"]
