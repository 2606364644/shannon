"""P3c 阶段 2 端到端：ws 配置 → scan 解析 → PipelineInput.provider_config。

PUT API（含 Fernet 加密落盘）→ scan_manager._resolve_provider_config → 提交的
PipelineInput.provider_config 全链生效；未配 ws 即使全局有配置也不能提交。
"""

import pytest


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
    (tmp_workspaces / "ws-a").mkdir()
    tok = authed_client.get("/api/auth/csrf").json()["csrf_token"]
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": (
            "SUPERNOVA_AI_PROVIDER=openai_compatible\n"
            "SUPERNOVA_OPENAI_API_KEY=sk-e2e\n"
            "SUPERNOVA_OPENAI_BASE_URL=https://llm-proxy.futuoa.com/v1\n"
            "SUPERNOVA_OPENAI_LARGE_MODEL=glm-5.2-coder\n"
            "SUPERNOVA_OPENAI_MEDIUM_MODEL=glm-5.2-coder\n"
            "SUPERNOVA_OPENAI_SMALL_MODEL=glm-5.2-coder\n"
            "SUPERNOVA_MAX_TURNS=555\n"
        )
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200

    # 核验凭据密文落盘（明文 api_key 不可见）
    raw = (tmp_workspaces / "ws-a" / "config.yaml").read_text()
    assert "sk-e2e" not in raw

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = app_with_ws.state.scan_manager
    sm._mark_submitted_at = lambda ws_dir: None
    scan_dir = tmp_workspaces / "ws-a" / "scans" / "s1"
    scan_dir.mkdir(parents=True, exist_ok=True)
    await sm._submit_whitebox(
        target="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir,
        event_file=tmp_workspaces / "ws-a" / "events.ndjson", web_url="")

    inp = captured["inp"]
    assert inp.provider_config["type"] == "openai_compatible"
    assert inp.provider_config["api_key"] == "sk-e2e"
    assert inp.provider_config["base_url"] == "https://llm-proxy.futuoa.com/v1"
    assert inp.provider_config["small_model"] == "glm-5.2-coder"
    assert inp.provider_config["medium_model"] == "glm-5.2-coder"
    assert inp.provider_config["large_model"] == "glm-5.2-coder"
    assert inp.provider_config["max_turns"] == 555


async def test_unconfigured_ws_cannot_submit_even_with_global_config(
    app_with_ws, tmp_workspaces, monkeypatch,
):
    """未配 ws → 即使全局有完整配置也不能提交。"""
    (tmp_workspaces / "ws-b").mkdir()
    monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("SUPERNOVA_OPENAI_API_KEY", "global-key")
    monkeypatch.setenv("SUPERNOVA_OPENAI_BASE_URL", "https://global.example/v1")
    monkeypatch.setenv("SUPERNOVA_OPENAI_SMALL_MODEL", "global-small")
    monkeypatch.setenv("SUPERNOVA_OPENAI_MEDIUM_MODEL", "global-medium")
    monkeypatch.setenv("SUPERNOVA_OPENAI_LARGE_MODEL", "global-large")

    captured: dict = {}
    _patch_connect(monkeypatch, captured)

    sm = app_with_ws.state.scan_manager
    sm._mark_submitted_at = lambda ws_dir: None
    scan_dir = tmp_workspaces / "ws-b" / "scans" / "s1"
    scan_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="SUPERNOVA_OPENAI_API_KEY"):
        await sm._submit_whitebox(
            target="/r", ws="ws-b", scan_id="s1", scan_dir=scan_dir,
            event_file=tmp_workspaces / "ws-b" / "events.ndjson", web_url="")

    assert "inp" not in captured
