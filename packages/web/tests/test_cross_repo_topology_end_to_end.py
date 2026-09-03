from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from supernova_core.agents.runner import ClaudeRunResult, TokenUsage
from supernova_core.config.parser import parse_multi_repo_config
from supernova_core.models.multi_repo_config import MultiRepoConfig
from supernova_multi.orchestrator import run_correlation_phase
from supernova_web.components.multi_repo_config_store import MultiRepoConfigStore
from supernova_web.components.scan_manager import ScanManager
from supernova_web.components.topology_analysis import TopologyAnalysisManager
from supernova_web.models import ScanRequest

REPOS = ("web", "admin", "order-svc", "user-svc")


class _FakeHandle:
    def __init__(self, tag: str) -> None:
        self.tag = tag


class _WorkerHandle:
    """fake WorkflowHandle：result() 直接跑真 activity（迁移后 worker 行为模拟）——
    e2e 验证 web 提交的 TopologyAnalysisInput 与 worker activity 消费完全契合。"""

    def __init__(self, inp):
        self.input = inp
        self.cancelled = False

    async def result(self):
        if self.cancelled:
            raise asyncio.CancelledError()
        from supernova_multi.pipeline import workflows as wf
        return await wf.run_topology_analysis_activity(self.input)

    async def cancel(self):
        self.cancelled = True

    async def describe(self):
        class _Desc:
            status = "RUNNING"
        return _Desc()


class _WorkerSimulatingTemporal:
    def __init__(self):
        self.submitted: list[tuple] = []

    async def connect(self):
        return self

    async def start_workflow(self, run, inp, *, id: str, task_queue: str):
        self.submitted.append((run, inp, id, task_queue))
        return _WorkerHandle(inp)

    def get_workflow_handle(self, workflow_id: str) -> _WorkerHandle:
        return _WorkerHandle(None)


def _seed_repo(root: Path, name: str) -> Path:
    repo = root / "ws" / "repos" / name
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    source = repo / "source.txt"
    source.write_text(f"{name} service\n", encoding="utf-8")
    return repo


def _analysis_payload() -> dict[str, Any]:
    return {
        "nodes": [
            {"repo": "web", "roles": ["entrypoint", "backend"], "capabilities": []},
            {"repo": "admin", "roles": ["entrypoint"], "capabilities": []},
            {"repo": "order-svc", "roles": ["backend"], "capabilities": []},
            {"repo": "user-svc", "roles": ["backend"], "capabilities": []},
        ],
        "edges": [
            {"from": "web", "to": "order-svc", "protocol": "grpc", "confidence": "high",
             "client_evidence": [{"repo": "web", "file": "source.txt", "line": 1, "snippet": f"{REPOS[0]} service"}],
             "handler_evidence": []},
            {"from": "admin", "to": "order-svc", "protocol": "graphql", "confidence": "medium",
             "client_evidence": [], "handler_evidence": []},
            {"from": "admin", "to": "user-svc", "protocol": "http", "confidence": "medium",
             "client_evidence": [], "handler_evidence": []},
        ],
        "uncertain": [],
        "coverage": [{"repo": name, "complete": True, "reason": "e2e fixture"} for name in REPOS],
    }


def _confirmed_yaml(analysis: dict[str, Any]) -> str:
    result = analysis["result"]
    roles = {node["repo"]: node["roles"] for node in result["nodes"]}
    edges = [f'  - {{from: {edge["from"]}, to: {edge["to"]}, protocol: {edge["protocol"]}}}'
             for edge in result["edges"]]
    # Human adjustment: add a second caller to user-svc, preserving all AI edges.
    edges.append('  - {from: web, to: user-svc, protocol: http}')
    repos = []
    for name in REPOS:
        effective = roles[name]
        repos.append(
            f"  {name}:\n    path: {name}\n    role: {effective[0]}\n"
            + (f"    roles: [{', '.join(effective)}]\n" if len(effective) > 1 else "")
        )
    return (
        "repos:\n" + "".join(repos)
        + "relations:\n" + "\n".join(edges) + "\n"
        + "correlation:\n  out_workspace: placeholder\n"
    )


@pytest.mark.asyncio
async def test_four_repo_analysis_confirmation_scan_and_correlation_deliverables(
    tmp_path: Path, monkeypatch,
):
    for name in REPOS:
        _seed_repo(tmp_path, name)

    async def topology_runner(**kwargs):
        assert kwargs["tool_policy"] == "readonly-code"
        assert {Path(root).name for root in kwargs["allowed_roots"]} == set(REPOS)
        return ClaudeRunResult(
            success=True,
            structured_output=_analysis_payload(),
            turns=4,
            tokens=TokenUsage(input_tokens=10, output_tokens=5),
        )

    # agent 在「worker 侧」执行：patch multi workflows 模块（activity 的 import 点）
    from supernova_multi.pipeline import workflows as multi_workflows
    monkeypatch.setattr(multi_workflows, "run_claude_prompt", topology_runner)
    temporal = _WorkerSimulatingTemporal()
    topology_manager = TopologyAnalysisManager(
        tmp_path, repo_manager=None, temporal_client_factory=temporal.connect)
    analysis_id = await topology_manager.start("ws", list(REPOS))
    await topology_manager.wait(analysis_id)
    analysis = topology_manager.api_view("ws", analysis_id)
    assert analysis["status"] == "completed"
    web_node = next(node for node in analysis["result"]["nodes"] if node["repo"] == "web")
    assert web_node["roles"] == ["entrypoint", "backend"]
    yaml_text = _confirmed_yaml(analysis)
    confirmed = parse_multi_repo_config_config(yaml_text)
    assert len(confirmed.relations) == 4

    submitted_whitebox: list[str] = []

    async def fake_submit_whitebox(self, target, ws, scan_id, scan_dir, event_file,
                                   web_url, combined=False):
        submitted_whitebox.append(Path(target).name)
        dlv = scan_dir / "deliverables"
        dlv.mkdir(parents=True, exist_ok=True)
        (dlv / "entry_points.json").write_text(json.dumps({"endpoints": []}), encoding="utf-8")
        (dlv / "injection_exploitation_queue.json").write_text(json.dumps({
            "vulnerabilities": [{
                "ID": f"INJ-{scan_id[-4:]}", "title": "E2E SQLi", "description": "fixture",
                "severity": "high", "location": "dao.py:1", "service": Path(target).name,
            }]
        }), encoding="utf-8")
        return _FakeHandle(f"wb:{scan_id}")

    async def fake_submit_correlation(self, config_path, repo_workspace_paths,
                                      out_ws_dir, event_file, ws):
        config = parse_multi_repo_config(config_path)
        assert len(config.relations) == 4

        async def fake_execute(self, agent_name=None, **kwargs):
            relation = json.loads(kwargs["prompt_variables"]["relations_json"])
            return type("Metrics", (), {"structured_output": {
                **relation, "calls": [], "status": "ok", "boundaries": [], "flows": [],
            }})()

        import supernova_core.agents.executor as executor_mod
        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", fake_execute)
        await run_correlation_phase(
            config, repo_workspace_paths, out_ws_dir, event_file, write_scan_end=False)
        return _FakeHandle("corr")

    async def fake_await(self, handle, attempts=5, backoff_base=2.0):
        return {"status": "completed"}

    async def temporal_ok():
        return None

    scan_manager = ScanManager(
        tmp_path, tmp_path / "legacy-repos", MultiRepoConfigStore(tmp_path / "configs"),
        max_concurrent=8,
    )
    monkeypatch.setattr(scan_manager, "_check_temporal", temporal_ok)
    monkeypatch.setattr(type(scan_manager), "_submit_whitebox", fake_submit_whitebox)
    monkeypatch.setattr(type(scan_manager), "_submit_correlation", fake_submit_correlation)
    monkeypatch.setattr(type(scan_manager), "_await_workflow_result", fake_await)

    ws_name, scan_id = await scan_manager.start(ScanRequest(
        type="correlation", workspace="ws", config_content=yaml_text))
    orchestrator = scan_manager._orchestrator_tasks[(ws_name, scan_id)]
    await orchestrator
    watch = scan_manager._tasks.get((ws_name, scan_id))
    if watch is not None:
        await asyncio.wait_for(asyncio.shield(watch), timeout=5)

    scans = scan_manager._store.list_scans("ws")
    main = next(scan for scan in scans if scan.scan_id == scan_id)
    assert main.scan_type == "correlation" and main.status == "completed"
    assert len(main.corr_children) == 4
    assert all(child["reused"] is False for child in main.corr_children)
    assert set(submitted_whitebox) == set(REPOS)

    scan_dir = tmp_path / "ws" / "scans" / scan_id
    deliverables = scan_dir / "deliverables"
    topology = json.loads((deliverables / "cross-service-topology.json").read_text(encoding="utf-8"))
    actual_edges = {(edge["from"], edge["to"], edge["protocol"]) for edge in topology["edges"]}
    assert ("web", "order-svc", "grpc") in actual_edges
    assert ("admin", "order-svc", "graphql") in actual_edges
    assert ("admin", "user-svc", "http") in actual_edges
    assert ("web", "user-svc", "http") in actual_edges
    assert (deliverables / "correlation-report.md").exists()
    assert (deliverables / "cross-service-flows.json").exists()
    assert (deliverables / "injection_exploitation_queue.json").exists()


def parse_multi_repo_config_config(text: str) -> MultiRepoConfig:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        return parse_multi_repo_config(Path(handle.name))
