"""P3c 阶段 2：scan_manager 按 ws 解析配置（替代阶段 1 全局构造）+ fail-fast。

直测 _submit_whitebox（隔离 start() 副作用），mock Client.connect 捕获 PipelineInput，
断言 provider_config 来自 ws 配置（非全局）。参照 test_scan_manager_provider_config.py。
"""
import pytest
import yaml

from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.ws_config_store import (
    WsConfigStore, WsConfig, WsProviderFields, default_ws_config,
)


def _patch_connect(monkeypatch, captured):
    class FakeHandle:
        pass

    class FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            captured["inp"] = inp
            return FakeHandle()

    async def fake_connect(addr):
        captured["connected"] = True
        return FakeClient()

    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)


async def test_submit_uses_ws_config(tmp_path, monkeypatch):
    """ws 配置的 ai_provider/api_key → 提交的 PipelineInput.provider_config。"""
    from supernova_web.components.scan_manager import ScanManager

    vault = CredentialVault(tmp_path / ".master_key")
    store = WsConfigStore(tmp_path, vault)
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", api_key="sk-ws",
        base_url="https://llm-proxy.futuoa.com/v1",
        small_model="glm-5.2-coder", medium_model="glm-5.2-coder",
        large_model="glm-5.2-coder")))

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object(),
                     ws_config_store=store)
    sm._mark_submitted_at = lambda ws_dir: None

    # T3: _submit_whitebox(target, ws, scan_id, scan_dir, event_file, web_url)
    scan_dir = tmp_path / "ws-a" / "scans" / "s1"; scan_dir.mkdir(parents=True, exist_ok=True)
    await sm._submit_whitebox(target="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir, event_file=tmp_path / "events.ndjson", web_url="")

    inp = captured["inp"]
    assert inp.provider_config["type"] == "openai_compatible"
    assert inp.provider_config["api_key"] == "sk-ws"


async def test_submit_fails_fast_on_missing_workspace_provider_config(tmp_path, monkeypatch):
    """默认模板缺 API key 时，不连接或提交 Temporal workflow。"""
    from supernova_web.components.scan_manager import ScanManager

    vault = CredentialVault(tmp_path / ".master_key")
    store = WsConfigStore(tmp_path, vault)
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", default_ws_config())

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object(),
                     ws_config_store=store)
    scan_dir = tmp_path / "ws-a" / "scans" / "s1"
    scan_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="SUPERNOVA_OPENAI_API_KEY"):
        await sm._submit_whitebox(
            target="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir,
            event_file=tmp_path / "events.ndjson", web_url="")
    assert "connected" not in captured
    assert "inp" not in captured


async def test_submit_fails_fast_on_invalid_ws_config(tmp_path, monkeypatch):
    """config.yaml 含非法 ai_provider（手动编辑/损坏）→ _resolve_provider_config 抛 ValueError，不提交。

    注意：store.write 内部就 validate 会拒 bogus，故用直接写 yaml 模拟绕过 store.write 的损坏配置。
    """
    from supernova_web.components.scan_manager import ScanManager

    vault = CredentialVault(tmp_path / ".master_key")
    store = WsConfigStore(tmp_path, vault)
    (tmp_path / "ws-a").mkdir()
    (tmp_path / "ws-a" / "config.yaml").write_text(
        yaml.safe_dump({"provider": {"ai_provider": "bogus"}}), encoding="utf-8")

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object(),
                     ws_config_store=store)

    # T3: _submit_whitebox(target, ws, scan_id, scan_dir, event_file, web_url)
    scan_dir = tmp_path / "ws-a" / "scans" / "s1"; scan_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        await sm._submit_whitebox(target="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir, event_file=tmp_path / "events.ndjson", web_url="")
    assert "inp" not in captured  # 未提交


async def test_submit_falls_back_when_ws_config_store_none(tmp_path, monkeypatch):
    """ws_config_store=None（CLI/旧测试）→ 全局 env 构造（阶段1 兜底，行为不变）。"""
    from supernova_web.components.scan_manager import ScanManager

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())  # ws_config_store=None
    sm._mark_submitted_at = lambda ws_dir: None

    # T3: _submit_whitebox(target, ws, scan_id, scan_dir, event_file, web_url)
    scan_dir = tmp_path / "ws-a" / "scans" / "s1"; scan_dir.mkdir(parents=True, exist_ok=True)
    await sm._submit_whitebox(target="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir, event_file=tmp_path / "events.ndjson", web_url="")

    inp = captured["inp"]
    assert inp.provider_config is not None
    assert inp.provider_config["type"] in (
        "anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router",
    )
