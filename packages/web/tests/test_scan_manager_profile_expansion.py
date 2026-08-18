"""选档案发起黑盒扫描:scan_manager 展开该角色 → scan-config.yaml(明文)。"""
import pytest
from unittest.mock import MagicMock
from supernova_web.models import ScanRequest
from supernova_web.components.scan_manager import ScanManager
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential)
from supernova_web.components.credential_vault import CredentialVault


@pytest.mark.asyncio
async def test_resolve_blackbox_expands_selected_profile(tmp_path):
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        login_flow=["成功标志:/dashboard"],
        credentials=[AuthProfileCredential(id="cred_a", role="admin", username="admin", password="pw")])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_1", auth_credential_id="cred_a")
    # 模拟 wb scan dir 存在
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    config_path, repo_path = await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    body = (scan_dir / "scan-config.yaml").read_text("utf-8")
    assert "admin" in body and "pw" in body and "/dashboard" in body
    assert config_path is not None


@pytest.mark.asyncio
async def test_resolve_blackbox_profile_missing_raises(tmp_path):
    """选不存在的档案 → ValueError(对齐 start_auth_validation 的口径)。"""
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_missing", auth_credential_id="cred_a")
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="认证档案不存在"):
        await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)


@pytest.mark.asyncio
async def test_resolve_blackbox_credential_missing_raises(tmp_path):
    """档案存在但角色 cred_id 不存在 → ValueError。"""
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[AuthProfileCredential(id="cred_a", role="admin", username="admin", password="pw")])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_1", auth_credential_id="cred_missing")
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="角色凭据不存在"):
        await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)


@pytest.mark.asyncio
async def test_resolve_blackbox_expands_all_credentials_multi_identity(tmp_path):
    """子项目2 T10：profile_id 无 cred_id → 多身份展开。

    选首个 low tier 作 primary（attacker），其余进 accounts[]（含 tier 标签）。
    cred_admin(admin)/cred_u1(user)/cred_u2(user) → primary=cred_u1（首个 low），
    accounts=[cred_admin(high), cred_u2(low)]。
    """
    import yaml
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        login_flow=["成功标志:/dashboard"],
        credentials=[
            AuthProfileCredential(id="cred_admin", role="admin", username="admin", password="pw"),
            AuthProfileCredential(id="cred_u1", role="user", username="u1", password="pw"),
            AuthProfileCredential(id="cred_u2", role="user", username="u2", password="pw"),
        ])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_1")  # 无 cred_id = 多身份模式
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    config_path, _ = await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    assert config_path is not None
    body = (scan_dir / "scan-config.yaml").read_text("utf-8")
    cfg = yaml.safe_load(body)
    assert "accounts" in cfg and len(cfg["accounts"]) == 2  # 其余 2 个（primary 进 authentication）
    tiers = {a["id"]: a["tier"] for a in cfg["accounts"]}
    assert tiers["cred-admin"] == "high"
    # primary = 首个 low = cred_u1（不进 accounts）；cred_u2 在 accounts 中为 low
    assert "cred-u1" not in tiers  # primary 不在 accounts
    assert tiers["cred-u2"] == "low"
    # authentication 应为 primary(cred_u1) 展开的单 credentials Authentication
    assert cfg["authentication"]["credentials"]["username"] == "u1"


@pytest.mark.asyncio
async def test_resolve_blackbox_multi_identity_all_high_falls_back_to_first(tmp_path):
    """无 low tier 时 primary 回落到首个 credential（兜底，不抛错）。"""
    import yaml
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[
            AuthProfileCredential(id="cred_a1", role="admin", username="a1", password="pw"),
            AuthProfileCredential(id="cred_a2", role="admin", username="a2", password="pw"),
        ])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1", auth_profile_id="prof_1")
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    config_path, _ = await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    body = (scan_dir / "scan-config.yaml").read_text("utf-8")
    cfg = yaml.safe_load(body)
    # 全 high → primary 回落首个(cred_a1) → accounts 只剩 cred_a2
    assert len(cfg["accounts"]) == 1
    assert cfg["accounts"][0]["id"] == "cred-a2"
    assert cfg["authentication"]["credentials"]["username"] == "a1"


@pytest.mark.asyncio
async def test_resolve_blackbox_multi_identity_profile_missing_raises(tmp_path):
    """多身份模式（无 cred_id）下选不存在的档案 → ValueError。"""
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_missing")
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="认证档案不存在"):
        await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)


@pytest.mark.asyncio
async def test_resolve_blackbox_multi_identity_no_store_raises(tmp_path):
    """多身份模式（无 cred_id）但 auth_profile_store 未注入 → RuntimeError（对齐单角色分支口径）。"""
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock())  # 无 auth_profile_store
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1", auth_profile_id="prof_1")
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="auth_profile_store 未注入"):
        await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)


@pytest.mark.asyncio
async def test_resolve_blackbox_expands_credential_ids_subset(tmp_path):
    """2026-08-06 多角色子集：profile_id + cred_ids[] → accounts[] 只含选中的角色。

    cred_admin(admin)/cred_u1(user)/cred_u2(user)，选 [cred_admin, cred_u1]：
    primary = 首个 low = cred_u1；accounts = [cred_admin(high)]；cred_u2 不出现。
    """
    import yaml
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[
            AuthProfileCredential(id="cred_admin", role="admin", username="admin", password="pw"),
            AuthProfileCredential(id="cred_u1", role="user", username="u1", password="pw"),
            AuthProfileCredential(id="cred_u2", role="user", username="u2", password="pw"),
        ])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_1",
                      auth_credential_ids=["cred_admin", "cred_u1"])  # 选 2/3
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    config_path, _ = await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    assert config_path is not None
    body = (scan_dir / "scan-config.yaml").read_text("utf-8")
    cfg = yaml.safe_load(body)
    # 选中 2 个 → primary 进 authentication，剩 1 个进 accounts
    assert "accounts" in cfg and len(cfg["accounts"]) == 1
    acct_ids = {a["id"] for a in cfg["accounts"]}
    assert acct_ids == {"cred-admin"}  # cred_u2 未选，不出现
    assert cfg["authentication"]["credentials"]["username"] == "u1"  # primary = 首个 low
    # 未选中的 cred_u2 完全不在 YAML
    assert "cred-u2" not in body


@pytest.mark.asyncio
async def test_resolve_blackbox_credential_ids_missing_raises(tmp_path):
    """子集模式：选中的 cred_ids 全不存在 → ValueError（无选中凭据）。"""
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[AuthProfileCredential(id="cred_a", role="admin", username="admin", password="pw")])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_1",
                      auth_credential_ids=["cred_missing"])
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="选中的角色凭据不存在"):
        await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)


@pytest.mark.asyncio
async def test_resolve_blackbox_multi_identity_accounts_carry_totp(tmp_path):
    """非 primary account 透传 totp_secret（2026-08-07 §3.4，多角色 2FA 不丢）。"""
    import yaml
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[
            AuthProfileCredential(id="cred_u1", role="user", username="u1", password="pw"),
            AuthProfileCredential(id="cred_admin", role="admin", username="adm",
                                  password="pw", totp_secret="TOTP"),
        ])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1", auth_profile_id="prof_1")
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    cfg = yaml.safe_load((scan_dir / "scan-config.yaml").read_text("utf-8"))
    # primary = 首个 low = cred_u1；cred_admin 进 accounts 且带 totp_secret
    acct = next(a for a in cfg["accounts"] if a["id"] == "cred-admin")
    assert acct["credentials"]["totp_secret"] == "TOTP"


@pytest.mark.asyncio
async def test_resolve_blackbox_credential_ids_subset_profile_missing_raises(tmp_path):
    """子集模式：档案不存在 → ValueError（对齐全角色/单角色分支口径）。"""
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_missing",
                      auth_credential_ids=["cred_a"])
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="认证档案不存在"):
        await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)


@pytest.mark.asyncio
async def test_multi_identity_account_ids_pass_core_parse_config(tmp_path):
    """回归（2026-08-17）：存量凭据 ID 含下划线（cred_xxx），展开进 accounts[] 时
    须清洗为 ^[a-z0-9-]+$ slug，否则认证预验证 parse_config 报
    "accounts[0].id 'cred_xxx' must match ^[a-z0-9-]+$"。

    走真实生成路径：upsert_profile 自动生成 cred_ + hex ID（带下划线）。
    """
    import yaml
    from supernova_core.config.parser import parse_config
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    profile = AuthProfile(
        id="prof_1", name="NodeGoat", login_url="http://t/", login_type="form",
        login_flow=["成功标志:/dashboard"],
        credentials=[
            AuthProfileCredential(id="", role="admin", username="admin", password="Admin_123"),
            AuthProfileCredential(id="", role="user", username="user1", password="User1_123"),
            AuthProfileCredential(id="", role="user", username="user2", password="User2_123"),
        ])
    profile = store.upsert_profile("ws1", profile)  # 自动生成 cred_xxx（下划线）
    assert all("_" in c.id for c in profile.credentials)  # 前置：ID 确含下划线
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1", auth_profile_id="prof_1")
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    config_path, _ = await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    cfg = yaml.safe_load((scan_dir / "scan-config.yaml").read_text("utf-8"))
    # accounts id = 清洗后的 slug（下划线 → 连字符），primary=user1 进 authentication
    acct_ids = [a["id"] for a in cfg["accounts"]]
    assert all("_" not in i for i in acct_ids)
    assert acct_ids == [profile.credentials[0].id.replace("_", "-"),
                        profile.credentials[2].id.replace("_", "-")]
    # 端到端：core parser 必须接受（修复前在此抛 PentestError）
    parsed = parse_config(config_path)
    assert {a.id for a in parsed.accounts} == set(acct_ids)
