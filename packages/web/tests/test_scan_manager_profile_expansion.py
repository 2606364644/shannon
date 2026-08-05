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
