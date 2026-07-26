"""P3c 阶段 2 端到端：ws 配置 → scan 解析 → PipelineInput.provider_config。

PUT API（含 Fernet 加密落盘）→ scan_manager._resolve_provider_config → 提交的
PipelineInput.provider_config 全链生效；未配 ws 回落全局默认。
"""


def _patch_connect(monkeypatch, captured):
    class FakeHandle:
        pass

    class FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            captured["inp"] = inp
            return FakeHandle()

    async def fake_connect(addr):
        return FakeClient()

    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)


async def test_ws_config_flows_to_pipeline_input(app_with_ws, authed_client, tmp_workspaces, monkeypatch):
    """PUT 写 ws 配置 → scan_manager 提交的 PipelineInput.provider_config 用 ws 配置。"""
    from supernova_web.models import ScanRequest

    (tmp_workspaces / "ws-a").mkdir()
    tok = authed_client.get("/api/auth/csrf").json()["csrf_token"]
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "provider": {"ai_provider": "openai_compatible", "api_key": "sk-e2e", "max_turns": 555}
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200

    # 核验凭据密文落盘（明文 api_key 不可见）
    raw = (tmp_workspaces / "ws-a" / "config.yaml").read_text()
    assert "sk-e2e" not in raw

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = app_with_ws.state.scan_manager
    sm._mark_submitted_at = lambda ws_dir: None
    req = ScanRequest(type="whitebox", workspace="ws-a")
    await sm._submit_whitebox(
        target="/r", ws="ws-a", event_file=tmp_workspaces / "ws-a" / "events.ndjson", req=req)

    inp = captured["inp"]
    assert inp.provider_config["type"] == "openai_compatible"
    assert inp.provider_config["api_key"] == "sk-e2e"
    assert inp.provider_config["max_turns"] == 555


async def test_unconfigured_ws_falls_back_to_global(app_with_ws, tmp_workspaces, monkeypatch):
    """未配 ws → PipelineInput.provider_config = 全局默认（build_provider_config）。"""
    from supernova_web.models import ScanRequest

    (tmp_workspaces / "ws-b").mkdir()

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = app_with_ws.state.scan_manager
    sm._mark_submitted_at = lambda ws_dir: None
    req = ScanRequest(type="whitebox", workspace="ws-b")
    await sm._submit_whitebox(
        target="/r", ws="ws-b", event_file=tmp_workspaces / "ws-b" / "events.ndjson", req=req)

    inp = captured["inp"]
    assert inp.provider_config is not None
    assert inp.provider_config["type"] in (
        "anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router",
    )
