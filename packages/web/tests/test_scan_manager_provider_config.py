"""P3c 阶段 1：scan_manager._submit_whitebox 提交时塞全局 provider_config。

直测 _submit_whitebox（隔离 start() 的 resolve_inputs/SessionManager 副作用）：
mock Client.connect 返假 client，捕获提交的 PipelineInput，断言 provider_config 非 None
（= 全局 env 构造，含合法 type）。web 路径 provider_config 非 None；阶段 2 改按 ws 解析。
"""


async def test_submit_whitebox_injects_global_provider_config(tmp_path, monkeypatch):
    from supernova_web.components.scan_manager import ScanManager

    captured: dict = {}

    class FakeHandle:
        pass

    class FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            captured["inp"] = inp
            return FakeHandle()

    async def fake_connect(addr):
        return FakeClient()

    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    sm._mark_submitted_at = lambda scan_dir: None  # 聚焦穿线，跳过 session.json 写

    # T3: _submit_whitebox(target, ws, scan_id, scan_dir, event_file, web_url)
    scan_dir = tmp_path / "ws-a" / "scans" / "s1"; scan_dir.mkdir(parents=True)
    await sm._submit_whitebox(
        target="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir,
        event_file=tmp_path / "events.ndjson", web_url="",
    )

    inp = captured.get("inp")
    assert inp is not None
    assert inp.provider_config is not None                       # web 路径非 None
    assert "type" in inp.provider_config                         # ProviderConfig dict
    assert inp.provider_config["type"] in (
        "anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router",
    )
