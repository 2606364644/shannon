"""CorrelationScanWorkflow 输入序列化 + activity 包装（不触真 Temporal server）。

workflow 逻辑仅一层 activity 直通——用 temporalio 的 Replayer/直接单测 activity 函数。
"""
import dataclasses
import json
from pathlib import Path


def test_pipeline_input_serializable():
    from supernova_multi.pipeline.shared import CorrelationPipelineInput
    inp = CorrelationPipelineInput(
        config_path="/tmp/multi-repo.yaml",
        repo_workspace_paths={"gateway": "/w/gw-scan"},
        out_ws_dir="/w/corr-1", event_file="/w/corr-1/events.ndjson",
        provider_config={"base_url": "x", "api_key": "k"},
        env_overrides={"FOO": "1"})
    # dataclass → dict → json 往返（Temporal 序列化等价）
    d = json.loads(json.dumps(dataclasses.asdict(inp)))
    assert d["repo_workspace_paths"]["gateway"] == "/w/gw-scan"
    assert d["write_scan_end"] is False


def test_activity_invokes_phase(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    from supernova_multi.pipeline.shared import CorrelationPipelineInput
    calls = {}

    async def fake_phase(config, repo_workspace_paths, out_ws_dir, event_file, **kw):
        calls["repo_paths"] = repo_workspace_paths
        calls["out_ws_dir"] = out_ws_dir
        calls["provider"] = kw.get("provider_config")
        calls["write_scan_end"] = kw.get("write_scan_end")
        return {"edge_statuses": [], "deliverables_path": str(out_ws_dir)}

    import asyncio
    monkeypatch.setattr(wf, "run_correlation_phase", fake_phase)
    cfg_file = tmp_path / "multi-repo.yaml"
    cfg_file.write_text(
        "repos:\n  gateway: {path: /r/gw, role: entrypoint}\n"
        "  b: {path: /r/b}\nrelations: []\n"
        "correlation: {out_workspace: corr-1}\n", encoding="utf-8")
    inp = CorrelationPipelineInput(
        config_path=str(cfg_file),
        repo_workspace_paths={"gateway": "/w/gw"},
        out_ws_dir="/w/corr-1", event_file="/w/corr-1/events.ndjson",
        provider_config={"api_key": "k"})
    result = asyncio.run(wf.run_correlation_activity(inp))
    assert calls["out_ws_dir"] == Path("/w/corr-1")
    assert calls["provider"] == {"api_key": "k"}
    assert calls["write_scan_end"] is False
    assert result["edge_statuses"] == []


def test_corr_task_queue_constant():
    from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_CORRELATION
    assert WEB_TASK_QUEUE_CORRELATION == "supernova-corr-web"


def test_runner_registers_corr_worker():
    """runner.run_worker 构造三个 Worker：corr 含 CorrelationScanWorkflow + activity。"""
    import inspect
    from supernova_worker import runner
    src = inspect.getsource(runner.run_worker)
    assert "WEB_TASK_QUEUE_CORRELATION" in src
    assert "CorrelationScanWorkflow" in src
    assert "run_correlation_activity" in src
