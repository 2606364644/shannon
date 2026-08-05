"""scan_manager 探针生命周期:写 probe YAML + 起 workflow + 取 result 回填 + 删 probe 目录。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, VerifyStatus,
)
from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.scan_manager import ScanManager
from supernova_core.services.validate_authentication import AuthValidationResult


def _store(tmp_path):
    vault = CredentialVault(tmp_path / ".master.key")
    s = AuthProfileStore(tmp_path, vault)
    s.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[AuthProfileCredential(id="cred_a", role="admin", username="admin", password="pw")])])
    return s


def _mgr(tmp_path, store):
    # 最小构造:scan_manager 只用到 _workspaces_dir / auth_profile_store / _temporal_address
    return ScanManager(
        workspaces_dir=tmp_path, repos_dir=tmp_path / "repos", config_store=MagicMock(),
        max_concurrent=1, scan_timeout=0.0, ws_config_store=MagicMock(),
        auth_profile_store=store,
    )


@pytest.mark.asyncio
async def test_start_auth_validation_writes_probe_yaml_and_starts_workflow(tmp_path):
    store = _store(tmp_path)
    mgr = _mgr(tmp_path, store)
    fake_handle = MagicMock()
    fake_handle.id = "wf-123"
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch("supernova_web.components.scan_manager.validate_authentication", create=True):
        ClientCls.connect = AsyncMock(return_value=MagicMock(
            start_workflow=AsyncMock(return_value=fake_handle)))
        wf_id = await mgr.start_auth_validation("ws1", "prof_1", "cred_a")
    assert wf_id["workflow_id"] == "wf-123"
    # probe 目录 + scan-config.yaml 被写(含 authentication 段,明文)
    probe_yamls = list((tmp_path / "ws1" / "auth-probes").glob("*/scan-config.yaml"))
    assert probe_yamls, "probe scan-config.yaml 应被写"
    body = probe_yamls[0].read_text("utf-8")
    assert "authentication" in body and "admin" in body and "pw" in body


@pytest.mark.asyncio
async def test_get_result_backfills_verify_status_and_deletes_probe(tmp_path):
    store = _store(tmp_path)
    mgr = _mgr(tmp_path, store)
    # 预置一个 probe 目录(模拟 start 已写)
    probe_dir = tmp_path / "ws1" / "auth-probes" / "probe-1"
    probe_dir.mkdir(parents=True)
    (probe_dir / "scan-config.yaml").write_text("authentication: {}", "utf-8")
    with patch("supernova_web.components.scan_manager.Client") as ClientCls:
        ClientCls.connect = AsyncMock(return_value=MagicMock(
            get_workflow_handle=MagicMock(return_value=MagicMock(
                result=AsyncMock(return_value=AuthValidationResult(
                    success=False, failure_point="username_or_password", failure_detail="bad pw"))))))
        status = await mgr.get_auth_validation_result(
            "ws1", workflow_id="wf-1", probe_dir=str(probe_dir),
            profile_id="prof_1", cred_id="cred_a",
        )
    assert status.state == "failed"
    assert status.failure_point == "username_or_password"
    # 回填进 store
    cred = store.read("ws1")[0].credentials[0]
    assert cred.verify_status.state == "failed"
    # probe 目录被删
    assert not probe_dir.exists()


@pytest.mark.asyncio
async def test_get_result_deletes_probe_dir_even_when_result_fetch_raises(tmp_path):
    """try/finally 不变量:Temporal result fetch 抛错时,明文 probe 目录也必清。"""
    store = _store(tmp_path)
    mgr = _mgr(tmp_path, store)
    probe_dir = tmp_path / "ws1" / "auth-probes" / "probe-err"
    probe_dir.mkdir(parents=True)
    (probe_dir / "scan-config.yaml").write_text(
        "authentication: {username: admin, password: leak-me}", "utf-8")
    with patch("supernova_web.components.scan_manager.Client") as ClientCls:
        ClientCls.connect = AsyncMock(side_effect=RuntimeError("temporal down"))
        with pytest.raises(RuntimeError, match="temporal down"):
            await mgr.get_auth_validation_result(
                "ws1", workflow_id="wf-x", probe_dir=str(probe_dir),
                profile_id="prof_1", cred_id="cred_a",
            )
    # 明文 probe 目录必清:即便 fetch 抛错（防明文密码滞留磁盘）
    assert not probe_dir.exists()
