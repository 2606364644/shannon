import pytest
from supernova_core.models.multi_repo_config import MultiRepoConfig, RepoSpec, Relation, CorrelationConfig
from supernova_multi.orchestrator import plan_repo_scans, RepoScanPlan


def _cfg(**overrides):
    repos = {
        "gateway": RepoSpec(path="/r/gw", role="entrypoint"),
        "order-svc": RepoSpec(path="/r/order", workspace="existing-order", role="backend"),
        "payment-svc": RepoSpec(path="/r/pay", role="backend"),
    }
    return MultiRepoConfig(
        repos=repos,
        relations=[Relation(**{"from": "gateway", "to": "order-svc"})],
        correlation=CorrelationConfig(out_workspace="out"),
        **overrides,
    )


def test_reuse_when_workspace_declared():
    plans = plan_repo_scans(_cfg())
    by_svc = {p.service: p for p in plans}
    # order-svc 声明了 workspace → 复用
    assert by_svc["order-svc"].reuse is True
    assert by_svc["order-svc"].workspace == "existing-order"
    # gateway 只给 path → 现扫
    assert by_svc["gateway"].reuse is False
    assert by_svc["gateway"].repo_path == "/r/gw"


def test_all_three_repos_planned():
    plans = plan_repo_scans(_cfg())
    assert {p.service for p in plans} == {"gateway", "order-svc", "payment-svc"}


# ---------------------------------------------------------------------------
# Task A6: per-edge asyncio + 单边隔离 + merge
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
from supernova_multi.orchestrator import _run_edge, _merge_edge_results  # noqa: E402


def _edge_result(from_, to, status="ok"):
    return {"from": from_, "to": to, "protocol": "grpc",
            "calls": [], "status": status, "boundaries": []}


@pytest.mark.asyncio
async def test_single_edge_failure_does_not_break_others():
    edges = [("gateway", "order-svc"), ("gateway", "payment-svc"), ("gateway", "broken-svc")]

    async def fake_edge(f, t):
        if t == "broken-svc":
            raise RuntimeError("boom")
        return _edge_result(f, t)

    results = await asyncio.gather(*[_run_edge(f, t, runner=fake_edge) for f, t in edges],
                                   return_exceptions=False)
    statuses = {r["status"] for r in results}
    # 失败的边标 error,其余 ok,不抛
    assert "error" in statuses
    assert "ok" in statuses
    assert len(results) == 3


def test_merge_edges_collects_all():
    merged = _merge_edge_results([_edge_result("g", "a"), _edge_result("g", "b")])
    assert len(merged["edges"]) == 2


def test_prompts_dir_is_absolute_and_points_to_real_prompts():
    """final-review IMPORTANT 1 回归锚点:prompts 路径必须绝对且指向真实 prompts 目录,
    防止非 repo-root CWD 调用时 Prompt file not found 崩溃回归。"""
    from supernova_multi.orchestrator import _prompts_dir
    d = _prompts_dir()
    assert d.is_absolute()
    assert (d / "cross-repo-correlation.txt").exists()


def test_write_correlation_deliverables_writes_all_files(tmp_path):
    """Task A6: report.py 落盘 helper 写齐四类产物。"""
    from supernova_core.correlation.report import write_correlation_deliverables
    from supernova_core.correlation.schemas import (
        CrossServiceTopology, ServiceNode, TopologyEdge, TrustBoundary,
    )
    topology = CrossServiceTopology(
        services=[ServiceNode(name="gateway", role="entrypoint", repo="/r/gw")],
        edges=[TopologyEdge(from_="gateway", to="order-svc", protocol="grpc")],
    )
    boundaries = [TrustBoundary(service="gateway", method="Checkout",
                                exposure="public", reachable_from=["*"],
                                reason="rbac", confidence="high")]
    merged_queues = {"injection": [
        {"title": "t", "description": "d", "severity": "high",
         "location": "f:1", "service": "gateway"}]}
    out = tmp_path / "deliverables"
    write_correlation_deliverables(out, topology, boundaries, merged_queues, "# report")
    assert (out / "cross-service-topology.json").exists()
    assert (out / "trust-boundaries.json").exists()
    assert (out / "correlation-report.md").read_text() == "# report"
    assert (out / "injection_exploitation_queue.json").exists()


# ---------------------------------------------------------------------------
# Task A3: run_correlation_phase 拆出(参数化 paths/event_file/provider/scan_end)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_correlation_phase_writes_flows_and_respects_paths(tmp_path, monkeypatch):
    """phase 参数化：repo_workspace_paths/out_ws_dir/event_file 全显式注入；
    write_scan_end=False 不写 scan_end；flows 落盘。Agent 以 stub 代（不打 LLM）。"""
    import json as _json
    from supernova_multi.orchestrator import run_correlation_phase

    # 两个子仓 workspace 目录：造 deliverables + 一个 queue
    gw_ws, be_ws = tmp_path / "gw-scan", tmp_path / "be-scan"
    for w in (gw_ws, be_ws):
        (w / "deliverables").mkdir(parents=True)
    (be_ws / "deliverables" / "injection_exploitation_queue.json").write_text(
        _json.dumps({"vulnerabilities": [
            {"title": "SQLi", "description": "d", "severity": "high",
             "location": "dao.go:8"}]}), encoding="utf-8")
    out_ws = tmp_path / "corr-scan"
    out_ws.mkdir()
    event_file = tmp_path / "corr-scan" / "events.ndjson"

    cfg = MultiRepoConfig(
        repos={"gateway": RepoSpec(path="/r/gw", role="entrypoint"),
               "order-svc": RepoSpec(path="/r/be", role="backend")},
        relations=[Relation(**{"from": "gateway", "to": "order-svc"})],
        correlation=CorrelationConfig(out_workspace="corr-scan"))

    async def fake_execute(self, **kw):
        class _M:  # 最小 metrics stub:edge_runner 只读 structured_output 属性
            structured_output = {
                "from": "gateway", "to": "order-svc", "protocol": "grpc",
                "calls": [], "status": "ok", "boundaries": [],
                "flows": [{"entry": "POST /orders",
                           "method": "order.v1.OrderService/CreateOrder",
                           "call_site": {"file": "c.ts", "line": 1, "snippet": "x"},
                           "vuln_refs": [{"service": "order-svc", "title": "SQLi",
                                           "severity": "high", "location": "dao.go:8"}],
                           "confidence": "high", "evidence": "e"}]}
        return _M()

    # patch 源头类(orchestrator 在函数内 import AgentExecutor,模块级无该属性)
    import supernova_core.agents.executor as executor_mod
    monkeypatch.setattr(executor_mod.AgentExecutor, "execute", fake_execute)

    result = await run_correlation_phase(
        cfg, {"gateway": gw_ws, "order-svc": be_ws}, out_ws, event_file,
        write_scan_end=False)

    dlv = out_ws / "deliverables"
    flows = _json.loads((dlv / "cross-service-flows.json").read_text(encoding="utf-8"))
    assert flows[0]["method"] == "order.v1.OrderService/CreateOrder"
    merged = _json.loads((dlv / "injection_exploitation_queue.json").read_text(encoding="utf-8"))
    assert merged["vulnerabilities"][0]["service"] == "order-svc"
    events = [_json.loads(l) for l in event_file.read_text(encoding="utf-8").splitlines() if l]
    assert all(e["type"] != "scan_end" for e in events)   # write_scan_end=False
    assert result["edge_statuses"] == ["ok"]


@pytest.mark.asyncio
async def test_run_correlation_phase_write_scan_end_true(tmp_path, monkeypatch):
    """write_scan_end=True（CLI 默认）→ scan_end 事件落 ndjson。"""
    from supernova_multi.orchestrator import run_correlation_phase
    import json as _json
    cfg = MultiRepoConfig(
        repos={"gateway": RepoSpec(path="/r/gw", role="entrypoint"),
               "order-svc": RepoSpec(path="/r/be", role="backend")},
        relations=[],  # 无边：不调 Agent，纯事件路径
        correlation=CorrelationConfig(out_workspace="corr-scan"))
    out_ws = tmp_path / "corr-scan"
    out_ws.mkdir()
    event_file = out_ws / "events.ndjson"
    await run_correlation_phase(cfg, {"gateway": out_ws}, out_ws, event_file,
                                write_scan_end=True)
    events = [_json.loads(l) for l in event_file.read_text(encoding="utf-8").splitlines() if l]
    assert any(e["type"] == "scan_end" and e["status"] == "completed" for e in events)
