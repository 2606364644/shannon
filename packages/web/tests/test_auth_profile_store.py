"""AuthProfileStore:读写 + 显式路径加密/解密 + 嵌套 email_login + 脱敏 + 空串保留。"""
from pathlib import Path

import pytest
import yaml

from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, EmailLoginCred,
    VerifyStatus, AlreadyForked,
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


# ---------------------------------------------------------------------------
# 系统级档案（scope=system，存 .system 段，所有 ws 共享）—— configs/*.yaml seed
# ---------------------------------------------------------------------------

def _system_profile():
    return AuthProfile(
        id="prof_sys", name="futunn", login_url="http://sys/", login_type="form",
        login_flow=["系统登录"],
        credentials=[AuthProfileCredential(id="cred_s", role="primary",
                                           username="sysuser", password="syspw")],
    )


def test_auth_profile_scope_defaults_to_workspace():
    # 向后兼容：旧档案（无 scope）默认 workspace
    p = AuthProfile(id="x", name="n", login_url="u", login_type="form",
                    credentials=[AuthProfileCredential(id="c", role="r", username="u")])
    assert p.scope == "workspace"


def test_get_falls_back_to_system_profile(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_system_profile()])  # 直接落盘到系统段
    # ws1 无该档案 → 透明 fallback 到 .system
    got = store.get("ws1", "prof_sys")
    assert got is not None
    assert got.scope == "system"
    assert got.name == "futunn"


def test_get_prefers_workspace_profile_over_system(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    # ws 与 .system 同 id，name 不同以区分来源
    ws_prof = AuthProfile(id="prof_dup", name="ws-side", login_url="http://w/",
                          login_type="form",
                          credentials=[AuthProfileCredential(id="c", role="r", username="u")])
    sys_prof = AuthProfile(id="prof_dup", name="sys-side", login_url="http://s/",
                           login_type="form", scope="system",
                           credentials=[AuthProfileCredential(id="c", role="r", username="u")])
    store.write("ws1", [ws_prof])
    store.write(".system", [sys_prof])
    got = store.get("ws1", "prof_dup")
    assert got.scope == "workspace"
    assert got.name == "ws-side"


def test_read_merges_system_profiles(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])              # ws 档案
    store.write(".system", [_system_profile()])   # 系统档案
    profiles = store.read("ws1")
    by_name = {p.name: p for p in profiles}
    assert "NodeGoat" in by_name      # ws
    assert "futunn" in by_name        # system（合并进来了）
    assert by_name["NodeGoat"].scope == "workspace"
    assert by_name["futunn"].scope == "system"


def test_read_system_segment_not_self_merged(tmp_path):
    # 防递归：read(".system") 只返回系统段自身，不再合并一次系统
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_system_profile()])
    profiles = store.read(".system")
    assert len(profiles) == 1


def test_read_masked_masks_system_profiles(tmp_path):
    # 系统档案在 GET 响应同样脱敏（不泄明文密码）
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_system_profile()])
    masked = store.read_masked("ws1")
    sys_cred = next(p for p in masked if p.name == "futunn").credentials[0]
    assert sys_cred.password == "••••"


# ---------------------------------------------------------------------------
# seed_from_config：configs/*.yaml → 系统级档案
# ---------------------------------------------------------------------------

_MINIMAL_AUTH_CONFIG = """\
authentication:
  login_type: form
  login_url: "http://example.com/login"
  credentials:
    username: "autoweb"
    password: "autoweb"
  login_flow:
    - "Navigate to the login page"
    - "Enter $username in the username field"
"""


def _write_config(configs_dir: Path, name: str, body: str) -> None:
    configs_dir.mkdir(parents=True, exist_ok=True)
    (configs_dir / name).write_text(body, "utf-8")


def test_seed_from_config_seeds_system_profile(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    configs = tmp_path / "configs"
    _write_config(configs, "futunn.yaml", _MINIMAL_AUTH_CONFIG)
    n = store.seed_from_config(configs)
    assert n == 1
    sys_profiles = store.read(".system")
    assert len(sys_profiles) == 1
    p = sys_profiles[0]
    assert p.name == "futunn"
    assert p.scope == "system"
    assert p.login_type == "form"
    assert p.login_url == "http://example.com/login"
    assert p.login_flow == ["Navigate to the login page",
                            "Enter $username in the username field"]
    assert p.credentials[0].role == "primary"
    assert p.credentials[0].username == "autoweb"
    assert p.credentials[0].password == "autoweb"   # 加密落盘后读回解密还原


def test_seed_skips_existing_by_name(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    configs = tmp_path / "configs"
    _write_config(configs, "futunn.yaml", _MINIMAL_AUTH_CONFIG)
    assert store.seed_from_config(configs) == 1
    # 二次 seed：.system 已有同名 futunn → 跳过，不覆盖、不重复
    assert store.seed_from_config(configs) == 0
    assert len(store.read(".system")) == 1


def test_seed_skips_no_authentication_yaml(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    configs = tmp_path / "configs"
    _write_config(configs, "noauth.yaml", "description: only desc\n")
    n = store.seed_from_config(configs)
    assert n == 0
    assert store.read(".system") == []


def test_seed_excludes_multi_and_users_yaml(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    configs = tmp_path / "configs"
    _write_config(configs, "web-multi-demo.yaml", _MINIMAL_AUTH_CONFIG)
    _write_config(configs, "users.yaml", _MINIMAL_AUTH_CONFIG)
    _write_config(configs, "users.yaml.example", _MINIMAL_AUTH_CONFIG)
    _write_config(configs, "futunn.yaml", _MINIMAL_AUTH_CONFIG)
    n = store.seed_from_config(configs)
    assert n == 1  # 仅 futunn；web-multi-* / users* 全排除
    names = {p.name for p in store.read(".system")}
    assert names == {"futunn"}


# ---------------------------------------------------------------------------
# set_verify_status 对称化：系统档案写回 .system，不在 ws 创副本
# ---------------------------------------------------------------------------

def test_set_verify_status_writes_back_to_system_for_system_profile(tmp_path):
    # 系统档案的 verify_status 应写回 .system，不在 ws 创副本
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_system_profile()])  # prof_sys / cred_s
    from supernova_web.components.auth_profile_store import VerifyStatus
    # 从 ws1 视角调（模拟 scan_manager 验证系统档案：store.get 透明 fallback 命中 .system）
    store.set_verify_status("ws1", "prof_sys", "cred_s", VerifyStatus(state="success"))
    # ws1 段无副本被创建
    assert store.read("ws1") == [] or all(p.scope == "system" for p in store.read("ws1"))
    assert store._read_segment("ws1") == []
    # .system 段已更新
    sys_p = store.read(".system")[0]
    assert sys_p.credentials[0].verify_status.state == "success"


def test_set_verify_status_writes_ws_profile_in_ws(tmp_path):
    # 回归：ws 档案 verify_status 仍写回 ws 段
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])  # prof_1 / cred_a
    from supernova_web.components.auth_profile_store import VerifyStatus
    store.set_verify_status("ws1", "prof_1", "cred_a", VerifyStatus(state="success"))
    ws_p = store._read_segment("ws1")[0]
    assert ws_p.credentials[0].verify_status.state == "success"
    # .system 段无副作用
    assert store._read_segment(".system") == []


# ---------------------------------------------------------------------------
# fork：read(ws) 按 profile.id 去重（ws 段覆盖 .system 同 id，不重复显示）
# ---------------------------------------------------------------------------

def test_read_dedups_by_id_ws_overrides_system(tmp_path):
    # fork 场景：ws 段与 .system 段同 id（ws 副本覆盖系统原型）。
    # read(ws) 该 id 只出现一次（ws 副本，scope=workspace），不重复系统原型。
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    ws_prof = AuthProfile(id="prof_dup", name="ws-side", login_url="http://w/",
                          login_type="form",
                          credentials=[AuthProfileCredential(id="c", role="r", username="u")])
    sys_prof = AuthProfile(id="prof_dup", name="sys-side", login_url="http://s/",
                           login_type="form", scope="system",
                           credentials=[AuthProfileCredential(id="c", role="r", username="u")])
    store.write("ws1", [ws_prof])
    store.write(".system", [sys_prof])
    profiles = store.read("ws1")
    dups = [p for p in profiles if p.id == "prof_dup"]
    assert len(dups) == 1                  # 不重复
    assert dups[0].scope == "workspace"    # ws 副本优先
    assert dups[0].name == "ws-side"


# ---------------------------------------------------------------------------
# fork：fork_from_system 把 .system 档案 fork 成 ws 段可编辑副本
# ---------------------------------------------------------------------------

def _verified_system_profile():
    """系统档案，credential 已验证成功 —— 证明 fork 后 verify_status 被重置。"""
    return AuthProfile(
        id="prof_sys", name="futunn", login_url="http://sys/", login_type="form",
        login_flow=["系统登录"], scope="system",
        credentials=[AuthProfileCredential(
            id="cred_s", role="primary", username="sysuser", password="syspw",
            email_login=EmailLoginCred(address="s@x.com", password="epw"),
            verify_status=VerifyStatus(state="success", last_verified_at="2026-01-01T00:00:00Z"))])


def test_fork_from_system_creates_editable_ws_copy(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_verified_system_profile()])
    forked = store.fork_from_system("ws1", "prof_sys")
    assert forked is not None
    assert forked.scope == "workspace"
    assert forked.id == "prof_sys"                       # profile.id 保留系统原 id
    c = forked.credentials[0]
    assert c.username == "sysuser"                       # 凭据明文相等
    assert c.password == "syspw"
    assert c.email_login.password == "epw"
    assert c.id != "cred_s"                              # credential.id 重新生成
    assert c.verify_status.state == "unverified"         # verify_status 重置
    assert c.verify_status.last_verified_at is None
    # 落盘到 ws 段（fork 副本已持久化，非仅内存）
    ws_seg = store._read_segment("ws1")
    assert any(p.id == "prof_sys" for p in ws_seg)


def test_fork_from_system_already_forked_raises(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_verified_system_profile()])
    store.fork_from_system("ws1", "prof_sys")   # 第一次 fork
    # ws 段已有同 id → 第二次 fork 拒绝（防覆盖用户已编辑的副本）
    with pytest.raises(AlreadyForked):
        store.fork_from_system("ws1", "prof_sys")


def test_fork_from_system_unknown_profile_returns_none(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_verified_system_profile()])
    # 系统段无该 id → None
    assert store.fork_from_system("ws1", "nope") is None
    # 未创建任何 ws 副本
    assert store._read_segment("ws1") == []


def test_get_after_fork_returns_ws_copy(tmp_path):
    # fork 后 get(ws, id) ws-priority 命中 ws 副本（不破现有不变量）
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_verified_system_profile()])
    store.fork_from_system("ws1", "prof_sys")
    got = store.get("ws1", "prof_sys")
    assert got is not None
    assert got.scope == "workspace"


def test_delete_fork_copy_reverts_to_system_view(tmp_path):
    # 删 ws 副本 → read(ws) 回到 system 原型视图（自然撤销 fork）
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_verified_system_profile()])
    store.fork_from_system("ws1", "prof_sys")
    assert store.delete_profile("ws1", "prof_sys")
    assert not any(p.id == "prof_sys" for p in store._read_segment("ws1"))
    view = store.read("ws1")
    dups = [p for p in view if p.id == "prof_sys"]
    assert len(dups) == 1
    assert dups[0].scope == "system"


def test_set_verify_status_on_fork_copy_writes_ws_not_system(tmp_path):
    # fork 副本 set_verify_status → 写回 ws 段，.system 原型不动（副本独立）
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write(".system", [_system_profile()])   # prof_sys/cred_s 默认 unverified
    forked = store.fork_from_system("ws1", "prof_sys")
    ws_cred_id = forked.credentials[0].id
    store.set_verify_status("ws1", "prof_sys", ws_cred_id, VerifyStatus(state="success"))
    ws_p = store._read_segment("ws1")[0]
    assert ws_p.credentials[0].verify_status.state == "success"        # ws 副本已更新
    sys_p = store._read_segment(".system")[0]
    assert sys_p.credentials[0].verify_status.state == "unverified"    # 系统原型未动
