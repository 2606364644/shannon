"""TopologyAnalysisWorkflow activity 编排（不触真 Temporal server）。

形态对齐 test_corr_workflow：直接单测 activity 函数 + monkeypatch 模块级
run_claude_prompt / PromptManager。锁定（spec 2026-09-03 §4.1/§4.2）：
status guard（非 queued 跳过防复写）、running/progress 写入、结果分类与
error.code（与 web 侧现状分类表逐一对齐——前端 TopologyAnalysisPanel 按
这些渲染）、usage 回填（result 优先、sink 兜底）、bb 队列注册面。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from supernova_core.agents.runner import ClaudeRunResult, TokenUsage
from supernova_core.topology.store import TopologyAnalysisStore


def _payload(repos: list[str]) -> dict:
    return {
        "nodes": [{"repo": repos[0], "roles": ["entrypoint", "backend"]}],
        "edges": [{
            "from": repos[0], "to": repos[1], "protocol": "grpc", "confidence": "medium",
            "client_evidence": [], "handler_evidence": [],
        }],
        "uncertain": [],
        "coverage": [{"repo": name, "complete": True, "reason": "test"} for name in repos],
    }


def _result(payload: dict | None = None, *, success: bool = True, error: str | None = None) -> ClaudeRunResult:
    return ClaudeRunResult(
        success=success,
        text="" if payload is not None else "not json",
        structured_output=payload,
        turns=3,
        cost=0.125,
        cost_currency="CNY",
        model="glm-test",
        tokens=TokenUsage(input_tokens=11, output_tokens=7, cache_read_input_tokens=3),
        error=error,
    )


def _seed(root: Path, ws: str = "ws1", analysis_id: str = "topology-aaaaaaaaaaaa",
          repos: tuple[str, ...] = ("gateway", "order-svc")) -> dict:
    """预置 queued state.json + 仓库目录（对齐 web _start 落盘形状）。"""
    repo_paths = {name: str(root / "workspaces" / ws / "repos" / name) for name in repos}
    for name in repos:
        (root / "workspaces" / ws / "repos" / name / ".git").mkdir(parents=True, exist_ok=True)
    state = {
        "analysis_id": analysis_id, "workspace": ws, "status": "queued",
        "repos": list(repos), "fingerprint": "f1", "fingerprint_detail": {},
        "manifest": {"repositories": list(repos)},
        "repo_paths": repo_paths,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "progress": 5, "cache_hit": False, "result": None, "raw_output": None,
        "usage": None, "error": None,
    }
    TopologyAnalysisStore(root / "workspaces").create(ws, state)
    return state


def _input(root: Path, state: dict, **over) -> "TopologyAnalysisInput":  # noqa: F821
    from supernova_multi.pipeline.shared import TopologyAnalysisInput
    kwargs = dict(
        analysis_id=state["analysis_id"], ws=state["workspace"],
        repos=state["repos"], repo_paths=state["repo_paths"],
        manifest=state["manifest"], provider_config=None,
        timeout_seconds=30.0, max_turns=30,
        workspaces_dir=str(root / "workspaces"),
    )
    kwargs.update(over)
    return TopologyAnalysisInput(**kwargs)


class _StubPromptManager:
    """记录 load_sync 入参，返回固定 prompt——测试不依赖真 prompts 文件。"""
    instances: list["_StubPromptManager"] = []

    def __init__(self, prompts_dir):
        self.prompts_dir = prompts_dir
        self.calls: list[tuple] = []
        _StubPromptManager.instances.append(self)

    def load_sync(self, name: str, variables: dict) -> str:
        self.calls.append((name, variables))
        return f"STUB-PROMPT[{name}]"


@pytest.fixture(autouse=True)
def _stub_prompts(monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    _StubPromptManager.instances = []
    monkeypatch.setattr(wf, "PromptManager", _StubPromptManager)


def test_topology_input_serializable():
    from supernova_multi.pipeline.shared import TopologyAnalysisInput
    inp = TopologyAnalysisInput(
        analysis_id="topology-abc123def456", ws="ws1",
        repos=["gateway", "order-svc"], repo_paths={"gateway": "/r/gateway"},
        manifest={"repositories": ["gateway"]}, provider_config={"api_key": "k"},
        env_overrides={"FOO": "1"}, timeout_seconds=900.0, max_turns=30,
        workspaces_dir="/workspaces",
    )
    d = json.loads(json.dumps(dataclasses.asdict(inp)))
    assert d["analysis_id"] == "topology-abc123def456"
    assert d["repo_paths"]["gateway"] == "/r/gateway"
    assert d["timeout_seconds"] == 900.0
    assert d["workspaces_dir"] == "/workspaces"


def test_activity_success_path(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)
    calls: dict = {}

    async def fake_runner(**kwargs):
        calls.update(kwargs)
        return _result(_payload(state["repos"]))

    monkeypatch.setattr(wf, "run_claude_prompt", fake_runner)
    out = asyncio.run(wf.run_topology_analysis_activity(_input(tmp_path, state)))

    store = TopologyAnalysisStore(tmp_path / "workspaces")
    final = store.get("ws1", state["analysis_id"])
    assert final["status"] == "completed"
    assert final["progress"] == 100
    assert final["result"]["edges"][0]["from"] == "gateway"
    assert final["raw_output"] == _payload(state["repos"])
    assert final["usage"] == {
        "input_tokens": 11, "output_tokens": 7, "cache_read_tokens": 3,
        "cache_creation_tokens": 0, "cost_usd": 0.125,
        "cost_currency": "CNY", "model": "glm-test", "turns": 3,
    }
    assert final["error"] is None
    assert out["status"] == "completed"
    # agent 调用参数对齐 web 现状 _run_agent（tool_policy/repo_path/allowed_roots/schema）
    assert calls["tool_policy"] == "readonly-code"
    assert calls["repo_path"] == str(store.path("ws1", state["analysis_id"]))
    assert calls["allowed_roots"] == [state["repo_paths"]["gateway"], state["repo_paths"]["order-svc"]]
    assert calls["structured_output_schema"] is not None
    assert calls["max_turns"] == 30
    # prompt worker 侧组装：manifest/repositories 经变量注入（web 不再碰 prompts）
    (name, variables), = _StubPromptManager.instances[0].calls
    assert name == "cross-repo-topology-discovery"
    assert json.loads(variables["repositories_json"]) == state["repo_paths"]
    assert json.loads(variables["navigation_manifest_json"]) == state["manifest"]
    assert (store.path("ws1", state["analysis_id"]) / "tool-audit.ndjson").exists()


def test_activity_status_guard_skips_cancelled(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)
    store = TopologyAnalysisStore(tmp_path / "workspaces")
    state.update({"status": "cancelled", "progress": 100, "error": {"code": "cancelled"}})
    store.write(state)

    async def fail_runner(**kwargs):
        raise AssertionError("runner must not be invoked for non-queued state")

    monkeypatch.setattr(wf, "run_claude_prompt", fail_runner)
    out = asyncio.run(wf.run_topology_analysis_activity(_input(tmp_path, state)))
    assert out["status"] == "skipped"
    final = store.get("ws1", state["analysis_id"])
    assert final["status"] == "cancelled"  # 终态未被复写


def test_activity_provider_failure(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)

    async def failing(**kw):
        return _result(None, success=False, error="provider down")

    monkeypatch.setattr(wf, "run_claude_prompt", failing)
    asyncio.run(wf.run_topology_analysis_activity(_input(tmp_path, state)))
    final = TopologyAnalysisStore(tmp_path / "workspaces").get("ws1", state["analysis_id"])
    assert final["status"] == "failed"
    assert final["error"]["code"] == "provider_failed"
    assert final["error"]["message"] == "provider down"
    assert final["usage"]["cost_usd"] == 0.125  # 失败也回填 usage


def test_activity_infra_exception_is_provider_failed(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)

    async def boom(**kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(wf, "run_claude_prompt", boom)
    asyncio.run(wf.run_topology_analysis_activity(_input(tmp_path, state)))
    final = TopologyAnalysisStore(tmp_path / "workspaces").get("ws1", state["analysis_id"])
    assert final["status"] == "failed"
    assert final["error"]["code"] == "provider_failed"
    assert final["error"]["message"] == "engine exploded"


def test_activity_malformed_output(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)

    async def no_json(**kw):
        return _result(None)

    monkeypatch.setattr(wf, "run_claude_prompt", no_json)
    asyncio.run(wf.run_topology_analysis_activity(_input(tmp_path, state)))
    final = TopologyAnalysisStore(tmp_path / "workspaces").get("ws1", state["analysis_id"])
    assert final["status"] == "failed"
    assert final["error"]["code"] == "malformed_output"


def test_activity_schema_invalid_payload(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)
    bad = _payload(state["repos"])
    bad["edges"][0]["confidence"] = "definitely"  # 非 enum → schema 校验失败

    async def bad_payload(**kw):
        return _result(bad)

    monkeypatch.setattr(wf, "run_claude_prompt", bad_payload)
    asyncio.run(wf.run_topology_analysis_activity(_input(tmp_path, state)))
    final = TopologyAnalysisStore(tmp_path / "workspaces").get("ws1", state["analysis_id"])
    assert final["status"] == "failed"
    assert final["error"]["code"] == "malformed_output"


def test_activity_timeout(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)

    async def slow(**kwargs):
        await asyncio.sleep(5)
        return _result(_payload(state["repos"]))

    monkeypatch.setattr(wf, "run_claude_prompt", slow)
    inp = _input(tmp_path, state, timeout_seconds=0.05)
    asyncio.run(wf.run_topology_analysis_activity(inp))
    final = TopologyAnalysisStore(tmp_path / "workspaces").get("ws1", state["analysis_id"])
    assert final["status"] == "failed"
    assert final["error"]["code"] == "timeout"
    assert "0.05" in final["error"]["message"]


def test_activity_cancelled_after_web_wrote_terminal_keeps_late_usage(tmp_path, monkeypatch):
    # R2 竞态收敛：真实时序是 activity 先过 guard 写 running → web cancel 写
    # cancelled 终态 → 取消才传播到 activity。终态不复写，sink 晚到的 usage
    # 保留（对齐 web 现状 CancelledError 分支语义）。
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)
    store = TopologyAnalysisStore(tmp_path / "workspaces")
    release = asyncio.Event()

    async def cancelled_runner(**kwargs):
        kwargs["usage_sink"].record(
            model="glm-test", input_tokens=5, output_tokens=3,
            cache_read_tokens=1, cache_creation_tokens=0,
            cost_usd=0.07, cost_currency="CNY")
        await release.wait()  # agent 在跑，等 web cancel 抢先写终态
        raise asyncio.CancelledError()

    monkeypatch.setattr(wf, "run_claude_prompt", cancelled_runner)

    async def scenario():
        task = asyncio.create_task(
            wf.run_topology_analysis_activity(_input(tmp_path, state)))
        await asyncio.sleep(0)  # 让 activity 过 guard 写 running、进 runner
        cancelled_state = store.get("ws1", state["analysis_id"])
        cancelled_state.update({"status": "cancelled", "progress": 100,
                                "error": {"code": "cancelled",
                                          "message": "analysis cancelled by user"}})
        store.write(cancelled_state)  # web cancel() 的「先写终态再 cancel」
        release.set()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
    final = store.get("ws1", state["analysis_id"])
    assert final["status"] == "cancelled"  # 晚到结果不复写终态
    assert final["usage"]["input_tokens"] == 5  # 但 usage 保留
    assert final["usage"]["cost_currency"] == "CNY"


def test_activity_cancelled_while_active_writes_cancelled_terminal(tmp_path, monkeypatch):
    # web 侧没来得及写终态（崩溃窗口）时 activity 收到取消——activity 兜底写 cancelled。
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path)
    store = TopologyAnalysisStore(tmp_path / "workspaces")

    async def parked_runner(**kwargs):
        await asyncio.Event().wait()  # 停在 await 点，等取消注入

    monkeypatch.setattr(wf, "run_claude_prompt", parked_runner)

    async def scenario():
        task = asyncio.create_task(
            wf.run_topology_analysis_activity(_input(tmp_path, state)))
        await asyncio.sleep(0)  # running 已写
        task.cancel()  # temporal cancel 传播（web 崩溃没写终态的窗口）
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
    final = store.get("ws1", state["analysis_id"])
    assert final["status"] == "cancelled"
    assert final["error"]["code"] == "cancelled"


def test_activity_state_missing_skips(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    state = _seed(tmp_path, analysis_id="topology-bbbbbbbbbbbb")
    inp = _input(tmp_path, state, analysis_id="topology-cccccccccccc")  # 无落盘 state

    async def must_not_run(**kw):
        pytest.fail("runner must not run without state")

    monkeypatch.setattr(wf, "run_claude_prompt", must_not_run)
    out = asyncio.run(wf.run_topology_analysis_activity(inp))
    assert out["status"] == "skipped"


def test_runner_registers_topology_analysis_on_bb_queue():
    import inspect
    from supernova_worker import runner
    src = inspect.getsource(runner.run_worker)
    # bb 队列（交互式轻任务的家，spec §3 队列全景）注册 workflow + activity
    assert "TopologyAnalysisWorkflow" in src
    assert "run_topology_analysis_activity" in src
    bb = src.split("WEB_TASK_QUEUE_BLACKBOX")[1].split("WEB_TASK_QUEUE_CORRELATION")[0]
    assert "TopologyAnalysisWorkflow" in bb
